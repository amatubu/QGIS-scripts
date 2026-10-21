# 必要なモジュールのインポート
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterFeatureSink,
    QgsFeature,
    QgsGeometry,
    QgsWkbTypes,
    QgsFeatureSink,
    QgsSpatialIndex,
    Qgis
)

# クラス定義 (プロセシングフレームワークを継承)
class HoleAwareDissolveAlgorithm(QgsProcessingAlgorithm):
    # パラメータに使用するID
    INPUT = 'INPUT'
    OUTPUT = 'OUTPUT'

    # 多言語対応
    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    # インスタンス生成
    def createInstance(self):
        return HoleAwareDissolveAlgorithm()

    # モジュール名(ユニークID)の定義
    def name(self):
        return 'holeawaredissolve'

    # ツールボックスに表示される名称の定義
    def displayName(self):
        return self.tr('穴を保護する条件付き隣接結合ツール (Hole-Aware Dissolve)')

    # ツールが属するグループの名称
    def group(self):
        return self.tr('Custom Polygon Tools')

    # ツールが属するグループのID
    def groupId(self):
        return 'custompolygontools'

    # ツールの説明(ツール実行時、右側に表示されるヘルプ)
    def shortHelpString(self):
        return self.tr(
            '<b>【概要】</b><br>'
            '分割された隣接ポリゴン同士を、元の穴の構造やトポロジを壊さない範囲で安全に結合（Dissolve）します。<br><br>'
            '<b>【入出力の仕様】</b><br>'
            '<ul>'
            '<li><b>入力:</b> シングルパートおよびマルチパートのポリゴンを受け付けます（マルチパートは自動的に分解されます）。</li>'
            '<li><b>出力:</b> 処理後の出力はすべてシングルパートのポリゴンになります。</li>'
            '<li><b>属性の継承:</b> 結合されたポリゴンの属性は、結合元となった一番小さなポリゴンのものが保持され、それ以外の属性は破棄されます。</li>'
            '</ul>'
            '<b>【留意事項】</b><br>'
            '高さ(Z)情報を含むレイヤを処理する場合、BlenderGISでの読み込み時に面が乱れることがあります。'
            '必要に応じてBlenderGISの「Separate objects」オプションを有効にして読み込み、'
            'Blender側で結合（Ctrl-J または Cmd-J）を行ってください。'
      )

    # ツールの概要(ツールチップとして表示される)
    def shortDescription(self):
      return self.tr('隣接するポリゴン同士を穴のトポロジを考慮して安全に結合します')
    # ツール実行画面の初期化(入力、出力パラメータの指定)
    def initAlgorithm(self, config=None):
        # 入力パラメータ
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT,                       # パラメータのID
                self.tr('入力ポリゴンレイヤ'),       # ラベル
                [QgsProcessing.TypeVectorPolygon] # ポリゴンレイヤのみにフィルタ
            )
        )
        # 出力パラメータ
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,                  # パラメータのID
                self.tr('結合済みポリゴンレイヤ') # ラベル
            )
        )

    # 処理アルゴリズムの定義
    #   parameters ... パラメータ定義
    #   context    ... QGISの環境情報(実行コンテクスト)
    #   feedback   ... ユーザ画面へのフィードバック(プログレスバー等の制御)
    def processAlgorithm(self, parameters, context, feedback):
        # パラメータから入力レイヤを取得
        source = self.parameterAsSource(
            parameters, # パラメータ定義
            self.INPUT, # パラメータID
            context     # 実行コンテクスト
        )
        
        # 入力のWKBタイプから、Z（高さ）が含まれているかどうかを判定
        input_wkb_type = source.wkbType()
        has_z = QgsWkbTypes.hasZ(input_wkb_type)
        
        # 出力用のWKBタイプを、Zの有無に応じてシングルパートの Polygon または PolygonZ に決定
        output_wkb_type = QgsWkbTypes.PolygonZ if has_z else QgsWkbTypes.Polygon
        
        # 出力用のレイヤを準備
        #   sink    ... 出力を受け付けるオブジェクト
        #   dest_id ... 出力先の識別ID
        (sink, dest_id) = self.parameterAsSink(
            parameters,          # パラメータ定義
            self.OUTPUT,         # パラメータID
            context,             # 実行コンテクスト
            source.fields(),     # フィールドの定義(入力レイヤと同じものを複写)
            output_wkb_type,     # レイヤの型の定義
            source.sourceCrs()   # 出力レイヤの座標系(入力レイヤと同じ)
        )

        feedback.pushInfo("=== Hole-Aware Dissolve 開始 ===")

        # 1. 入力レイヤの地物を読み込み、マルチパートならパーツごとに分解してリストに格納
        # 元となるポリゴンのリスト
        initial_polygons = []
        # ポリゴンのIDを記録するためのカウンタ
        feature_id_counter = 0

        # 入力レイヤに含まれる地物をひとつひとつ取り出して繰り返す
        #   feature ... 取り出した地物オブジェクト
        for feature in source.getFeatures():
            # キャンセルされたらループを抜ける
            if feedback.isCanceled():
                break

            # 地物データから形状(ジオメトリ)を取得
            geom = feature.geometry()

            # 形状(ジオメトリ)がない、または空のときは次の地物へ
            if not geom or geom.isEmpty():
                continue

            # シングルパートのリストに展開
            parts = [] # 空のリストを作成
            if geom.isMultipart():
                # 地物がマルチパート(複数のパーツに分かれている)ならばひとつひとつのパーツに対して処理
                for part in geom.asGeometryCollection():
                    # リストにパーツを追加
                    parts.append(QgsGeometry(part))
            else:
                # リストにジオメトリを追加
                #   QgisGeometryで新しいインスタンスを作成してリストに加える
                parts.append(QgsGeometry(geom))

            # 各パーツを管理用の辞書構造にして初期リストに追加
            for part_geom in parts:
                if not part_geom.isEmpty():
                    initial_polygons.append({
                        'id': feature_id_counter,     # ユニークなID(カウンタ)
                        'geom': part_geom,            # ジオメトリ
                        'attr': feature.attributes(), # 属性も保持
                        'area': part_geom.area()      # 面積
                    })
                    # カウンタを加算
                    feature_id_counter += 1

        # 処理するポリゴンの数
        initial_count = len(initial_polygons)
        feedback.pushInfo(f"処理対象のパーツ総数（展開後）: {initial_count} 件")

        # 2. 条件付き隣接結合（Greedy Dissolve）のメインループ
        # 処理すべきポリゴンのリスト(=元のポリゴンリスト)
        active_polygons = initial_polygons
        # 処理が終わった(結合相手がいない)ポリゴンのリスト
        completed_polygons = []

        # 進捗管理用の総数
        # 処理すべきポリゴンがないときは1にする(進捗表示での0除算を避けるため)
        total_parts_for_progress = initial_count if initial_count else 1

        # 処理すべきポリゴンがなくなるまで繰り返す
        while active_polygons:
            # キャンセルされたらループを抜ける
            if feedback.isCanceled():
                break

            # 常に「面積が小さい順」にソートして、細かい破片から優先的に周囲に吸収させる
            # lambda は無名関数の定義で、与えられたポリゴンリストに対して面積を返す
            active_polygons.sort(key=lambda p: p['area'])
            
            # 処理すべきポリゴンについて、IDをキーにポリゴンを取り出すための辞書を作成
            poly_map = {p['id']: p for p in active_polygons}

            # 現在のアクティブリストで空間インデックスを構築
            spatial_index = QgsSpatialIndex()
            for p in active_polygons:
                # QgsSpatialIndex に登録するための軽量な Feature を作成
                feat = QgsFeature()
                feat.setId(p['id'])         # ID(交差する地物のIDとして得られる)
                feat.setGeometry(p['geom']) # ジオメトリ
                # 空間インデックスに地物を追加
                spatial_index.addFeature(feat)

            # 最も面積が小さいポリゴンを取り出す
            p1 = active_polygons[0]
            # 結合相手がいたかどうかの判別フラグ
            found_merge_partner = False
            
            # 空間インデックスからバウンディングボックスが交差する近傍候補を取得
            candidate_ids = spatial_index.intersects(p1['geom'].boundingBox())
            
            for cid in candidate_ids:
                # 自分自身、またはすでに削除済みのIDはスキップ
                # intersects は自分自身も含まれる
                if cid == p1['id'] or cid not in poly_map:
                    continue

                # 結合候補のポリゴン
                p2 = poly_map[cid]
                
                # 隣接（接している）かチェック
                if p1['geom'].touches(p2['geom']):
                    try:
                        # 結合してみる
                        combined_geom = p1['geom'].combine(p2['geom'])
                    except:
                        continue
                    
                    # ジオメトリの実体（QgsAbstractGeometry）を取得して安全チェック
                    abs1 = p1['geom'].constGet()
                    abs2 = p2['geom'].constGet()
                    abs_comb = combined_geom.constGet()
                    
                    # 結合元もしくは結合後のジオメトリに異常があれば次へすすむ
                    if not abs1 or not abs2 or not abs_comb:
                        continue
                    
                    # 結合後の妥当性と穴の数の変化をチェック
                    if combined_geom.isGeosValid():
                        # それぞれの穴の数をカウント
                        holes1 = abs1.numInteriorRings()
                        holes2 = abs2.numInteriorRings()
                        holes_comb = abs_comb.numInteriorRings()
                        
                        # 安全条件：結合後の穴の数が、結合前の合計を超えて増えていなければ(=新たな穴が生じてなければ)合格！
                        if holes_comb <= (holes1 + holes2):
                            # ── 結合成功 ──
                            # p1 の形状と面積を更新
                            p1['geom'] = combined_geom
                            p1['area'] = combined_geom.area()
                            
                            # 結合相手 (p2) をアクティブリストから削除
                            active_polygons = [p for p in active_polygons if p['id'] != p2['id']]

                            # 結合相手が見つかった
                            found_merge_partner = True
                            break # 小さいパーツ p1 が大きくなったので、外側ループで再ソートへ
            
            if not found_merge_partner:
                # 誰とも結合できなかった場合：
                # このパーツはこれ以上結合できない「確定版」として完了リストへ移動
                # 処理すべきポリゴンリストからはpop()で除かれる
                completed_polygons.append(active_polygons.pop(0))
                
                # 進捗状況をフィードバックに反映
                # 基本的に initial_count == total_parts_for_progress だが、0除算を避けるため分母は先に置き換えたものを使用
                progress = int((initial_count - len(active_polygons)) / total_parts_for_progress * 100)
                feedback.setProgress(progress)

        # 3. 結果の出力
        # 処理が終わったあとのポリゴンをカウント
        final_count = len(completed_polygons)
        feedback.pushInfo(f"結合完了後の最終パーツ数: {final_count} 件 (削減数: {initial_count - final_count} 件)")

        # 処理済みのポリゴンを出力用レイヤに書き込む
        for out_item in completed_polygons:
            # 新しい地物を作成
            new_feat = QgsFeature()
            # フィールド定義の設定
            new_feat.setFields(source.fields())
            # フィールドの値を設定(結合前の値を複写。ただし結合された元が一番狭かった地物のものだけが残る)
            new_feat.setAttributes(out_item['attr'])
            # ジオメトリ情報を設定
            new_feat.setGeometry(out_item['geom'])
            # 出力レイヤに地物を追加
            # QgsFeatureSink.FastInsert は高速処理
            sink.addFeature(new_feat, QgsFeatureSink.FastInsert)

        # 処理結果の表示
        feedback.pushInfo("=== 処理完了統計 ===")
        feedback.pushInfo(f"  - 入力パーツ数: {initial_count} 件")
        feedback.pushInfo(f"  - 結合後の出力パーツ数: {final_count} 件")
        feedback.pushInfo("=====================================")

        # 作成されたレイヤのIDを出力(OUTPUT)パラメータに返す
        return {self.OUTPUT: dest_id}