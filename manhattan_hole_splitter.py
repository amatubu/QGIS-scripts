# 必要なモジュールのインポート
from qgis.PyQt.QtCore import QCoreApplication #
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterFeatureSink,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsWkbTypes,
    QgsFeatureSink,
    Qgis
)

# クラス定義
# プロセシングフレームワークを継承
class ManhattanHoleSplitterAlgorithm(QgsProcessingAlgorithm):
    # パラメータに使用するID
    INPUT = 'INPUT'
    OUTPUT = 'OUTPUT'

    # 多言語対応
    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    # インスタンス生成
    def createInstance(self):
        return ManhattanHoleSplitterAlgorithm()

    # モジュール名(ユニークID)の定義
    def name(self):
        return 'manhattanholesplitter'

    # ツールボックスに表示される名称の定義
    def displayName(self):
        return self.tr('階段状に穴を分割するツール (Manhattan Hole Splitter)')

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
            '穴あきポリゴンの形状（縦長/横長）を判定し、穴の重心同士を水平・垂直の階段状ラインで一網打尽に切断します。<br><br>'
            '<b>【入出力の仕様】</b><br>'
            '<ul>'
            '<li><b>入力:</b> シングルパートおよびマルチパートのポリゴンを受け付けます。</li>'
            '<li><b>出力:</b> 穴のないパーツや地物は元の構造を維持してパススルーし、分割処理が行われたパーツはシングルパートのポリゴンとして出力されます。</li>'
            '</ul>'
            '<b>【留意事項】</b><br>'
            '高さ(Z)情報のついたポリゴンにも対応していますが、BlenderGISでの読み込み時に面が乱れることがあります。'
            '高さ(Z)情報が必要な場合は当処理のあとで付与するか、BlenderGISの「Separete objects」オプションを'
            '有効にして読み込み結合(Ctrl-J または Cmd-J)することで回避可能です。'
        )

    # ツールの概要(ツールチップとして表示される)
    def shortDescription(self):
        return self.tr('穴あきポリゴンを階段状ラインで効率よく分割します')

    # ツール実行画面の初期化(入力、出力パラメータの指定)
    def initAlgorithm(self, config=None):
        # 入力パラメータ
        self.addParameter(
            # ベクタレイヤのドロップダウンを作成
            QgsProcessingParameterVectorLayer(
                self.INPUT,                       # パラメータのID
                self.tr('入力ポリゴンレイヤ'),       # ラベル
                [QgsProcessing.TypeVectorPolygon] # ポリゴンレイヤのみにフィルタ
            )
        )
        # 出力パラメータ
        self.addParameter(
            # 出力するレイヤの入れ物を用意
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,                  # パラメータのID
                self.tr('分割済みポリゴンレイヤ') # ラベル
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
            input_wkb_type,      # レイヤの型の定義
            source.sourceCrs()   # 出力レイヤの座標系(入力レイヤと同じ)
        )

        # 進捗表示のための変数定義
        #   100% を入力レイヤの地物の数で割った値
        #   地物が0件の場合は0とする
        total = 100.0 / source.featureCount() if source.featureCount() else 0

        # 処理する地物のカウント
        total_input_count = source.featureCount()
        feedback.pushInfo("=== Manhattan Hole Splitter 開始 ===")
        feedback.pushInfo(f"処理対象の入力地物総数: {total_input_count} 件")

        total_output_count = 0 # 最終的な総数カウンタ
        skipped_count      = 0 # 穴がなくスキップした地物のカウンタ

        # 入力レイヤに含まれる地物をひとつひとつ取り出して繰り返す
        #   current ... インデックス
        #   feature ... 取り出した地物オブジェクト
        for current, feature in enumerate(source.getFeatures()):
            # キャンセルされたらループを抜ける
            if feedback.isCanceled():
                break

            # 地物データから形状(ジオメトリ)と属性を取得
            geom = feature.geometry()
            fields = feature.attributes()

            # 形状(ジオメトリ)がない、または空のときは次の地物へ
            if not geom or geom.isEmpty():
                skipped_count += 1
                continue

            # シングルパートのリストに展開
            polygons_to_process = []  # 空のリストを作成
            num_holes = 0  # 穴の数
            if geom.isMultipart():
                # 地物がマルチパート(複数のパーツに分かれている)ならばひとつひとつのパーツに対して処理
                for part in geom.asGeometryCollection():
                    # リストにパーツを追加(仮)
                    polygons_to_process.append(part)
                    ab = part.constGet()
                    if ab:
                        # 穴の数をカウント
                        num_holes += ab.numInteriorRings()
            else:
                ab = geom.constGet()
                if ab:
                    # 穴の数をカウント
                    num_holes = ab.numInteriorRings()
                    if num_holes > 0:
                        # リストにジオメトリを追加
                        #   QgisGeometryで新しいインスタンスを作成してリストに加える
                        polygons_to_process.append(QgsGeometry(geom))

            if num_holes == 0:
                # 穴がなければ地物をそのまま追加して次へ
                sink.addFeature(feature, QgsFeatureSink.FastInsert)
                skipped_count += 1
                continue

            # 処理済みのポリゴンを格納するリスト(最初は空)
            processed_polygons = []

            # 各地物(またはパーツ)を処理
            for p_geom in polygons_to_process:
                # 処理前の段階で、このパーツが持っている穴の総数をあらかじめカウントしておく
                initial_abstract = p_geom.constGet()
                initial_holes = initial_abstract.numInteriorRings() if initial_abstract else 0

                # 処理すべき地物をキューに格納
                # 理論的には一度の分割で処理できるはずだが、分割後に穴が残っていないか確認するために
                # キューを用いて漏れがないように処理している
                poly_queue = [p_geom]

                # キューが空になるまで繰り返す
                while poly_queue:
                    # キューから地物を取り出す
                    current_poly = poly_queue.pop(0)
                    # ジオメトリの実体(QgsAbstractGeometry)を取得
                    abstract_geom = current_poly.constGet()
                    
                    # 穴（内リング）の数を取得 (numInteriorRings で穴の数を得る)
                    #   ジオメトリがない場合は0とする
                    num_holes = abstract_geom.numInteriorRings() if abstract_geom else 0
                    
                    if num_holes > 0:
                        # 1. すべての穴の重心を取得
                        # 重心の座標を格納するリスト
                        hole_centers = []
                        # 穴の数だけ繰り返し
                        for i in range(num_holes):
                            # 内リング（穴）ジオメトリの取得 (interiorRing で穴のリング(ライン)を取得)
                            h_ring = abstract_geom.interiorRing(i)
                            # 穴のラインを複写してQgsGeomtryクラスのオブジェクトに変換
                            h_geom = QgsGeometry(h_ring.clone())
                            # 穴の重心を得る(内部的にはポリゴンとしての重心が得られる)
                            #   参照
                            #   https://api.qgis.org/api/3.44/classQgsGeometry.html#a9af6067caef47d380d45c32569abc10b
                            c = h_geom.centroid().asPoint()
                            # 得られた重心の位置をリストに追加
                            hole_centers.append(c)
                        
                        # ポリゴンの外接四角形(バウンディングボックスを取得)
                        bbox = current_poly.boundingBox()
                        # 得られた四角形の幅(東西の距離)と高さ(南北の距離)を得る
                        width = bbox.width()
                        height = bbox.height()
                        
                        # 十分な長さのマージン(200m)を設定
                        margin = 200.0
                        # ポリゴンを囲う四角形からマージン分だけ広げた領域の座標を得る
                        # これらの座標を目指して、最初と最後のカットラインのポイントを指定する
                        min_x = bbox.xMinimum() - margin
                        max_x = bbox.xMaximum() + margin
                        min_y = bbox.yMinimum() - margin
                        max_y = bbox.yMaximum() + margin
                        
                        # カットラインの頂点リスト
                        points = []
                        
                        # 2. 地物が横長か縦長かを判定（短軸方向にカットしてライン長を短縮）
                        if width >= height:
                            # 【横長地物】: 縦方向（北から南）に貫通させる
                            # X座標（西から東）順にソート
                            # lambda は無名関数の定義で、与えられたポイントに対してX座標を返す
                            hole_centers.sort(key=lambda pt: pt.x())
                            
                            # スタート（上(北)の外側から最初の穴のX座標へ）
                            points.append(QgsPointXY(hole_centers[0].x(), max_y))
                            # 次の点は1番目の穴の重心
                            points.append(hole_centers[0])
                            
                            # 穴と穴の間を「垂直(南北) ➔ 水平(東西)」の階段状に繋ぐ
                            # 穴の数-1まで繰り返す(穴と穴の間の数=穴の数-1)
                            for i in range(len(hole_centers) - 1):
                                # 現在の穴の重心
                                p_curr = hole_centers[i]
                                # 次の穴の重心
                                p_next = hole_centers[i+1]
                                # 中継点（前のYから次のYへ垂直(南北)に進み、次のXへ水平(東西)に折れる）
                                points.append(QgsPointXY(p_curr.x(), p_next.y()))
                                # 次の穴の重心へ
                                points.append(p_next)
                                
                            # ゴール（最後の穴から下(南)の外側へ抜ける）
                            # -1でリストの最後が得られる
                            last_pt = hole_centers[-1]
                            # 最後の穴の重心のX座標(東西)、南端のY座標
                            points.append(QgsPointXY(last_pt.x(), min_y))
                            
                        else:
                            # 【縦長地物】: 横方向（西から東）に貫通させる
                            # Y座標（南から北）順にソート
                            # lambda は無名関数の定義で、与えられたポイントに対してY座標を返す
                            hole_centers.sort(key=lambda pt: pt.y())
                            
                            # スタート（左(西)の外側から最初の穴のY座標へ）
                            points.append(QgsPointXY(min_x, hole_centers[0].y()))
                            # 次の点は1番目の穴の重心
                            points.append(hole_centers[0])
                            
                            # 穴と穴の間を「水平(東西) ➔ 垂直(南北)」の階段状に繋ぐ
                            # 穴の数-1まで繰り返す(穴と穴の間の数=穴の数-1)
                            for i in range(len(hole_centers) - 1):
                                # 現在の穴の重心
                                p_curr = hole_centers[i]
                                # 次の穴の重心
                                p_next = hole_centers[i+1]
                                # 中継点（前のXから次のXへ水平(東西)に進み、次のYへ垂直(南北)に折れる）
                                points.append(QgsPointXY(p_next.x(), p_curr.y()))
                                # 次の穴の重心へ
                                points.append(p_next)
                                
                            # ゴール（最後の穴から右(東)の外側へ抜ける）
                            # -1でリストの最後が得られる    
                            last_pt = hole_centers[-1]
                            # 最後の穴の重心のX座標(東西)、南端のY座標
                            points.append(QgsPointXY(max_x, last_pt.y()))

                        # 3. カッターライン（折れ線）の作成
                        # .fromPolylineXYで点のリストから折れ線のジオメトリを生成
                        cutter_line = QgsGeometry.fromPolylineXY(points)

                        # 4. 分割の実行
                        # 参照
                        # https://api.qgis.org/api/3.44/classQgsGeometry.html#aaae079efa718628e2af10cb0abd73077
                        #   出力のひとつめは実行結果(0は成功)
                        #   参照
                        #   https://api.qgis.org/api/3.44/classQgis.html#a2231cf0dcd15b05248b74439d64dc06e
                        #   出力のふたつめは分割されてできたポリゴンのリスト
                        #   current_polyも編集されてひとつめのポリゴンが得られる
                        split_result, new_geoms, _ = current_poly.splitGeometry(
                            cutter_line.asPolyline(), # カットライン
                            False                     # トポロジカル編集無効
                        )

                        if split_result == Qgis.GeometryOperationResult.Success:
                            # 分割成功
                            # 分割してできたポリゴンを再度キューに登録して念のため再チェックへ
                            # 分割されたひとつめのポリゴン(current_polyに上書きされたもの)をキューに追加
                            poly_queue.append(current_poly)
                            # 残りのポリゴンをnew_geomsから取り出してキューに追加
                            for new_g in new_geoms:
                                poly_queue.append(new_g)
                        else:
                            # 分割失敗時のフォールバック
                            # 分割できなかったときは、処理済みとする
                            processed_polygons.append(current_poly)
                    else: # if num_holes > 0:
                        # 穴がなければ処理済みとする
                        processed_polygons.append(current_poly)

                # ── 処理が完了したタイミングで、最初の穴の数と生成数を1行でスッキリ出力 ──
                feedback.pushInfo(f"[地物 #{current + 1}] 穴の数: {initial_holes} 個 ➔ 生成ポリゴン数: {len(processed_polygons)} 個")

            # 結果の出力
            # 処理済みのポリゴンリストのひとつひとつについて処理する
            for out_geom in processed_polygons:
                # 新しい地物を作成
                new_feat = QgsFeature()
                # フィールド定義の設定
                new_feat.setFields(source.fields())
                # フィールドの値を設定(分割前の値を複写)
                new_feat.setAttributes(fields)
                # ジオメトリ情報を設定
                new_feat.setGeometry(out_geom)
                # 出力レイヤに地物を追加
                # QgsFeatureSink.FastInsert は高速処理
                sink.addFeature(new_feat, QgsFeatureSink.FastInsert)

            # 処理結果の表示(マルチパートで複数のパートがある場合は計を表示)
            if geom.isMultipart() and len(polygons_to_process) > 1:
                feedback.pushInfo(f"  [地物 #{current + 1} 計] 生成されたポリゴン数: {len(processed_polygons)} 個")

            # 総合計に今回の数を加算
            total_output_count += len(processed_polygons)

            # プログレスバーの表示
            feedback.setProgress(int(current * total))

        # 最後にまとめて集計結果を表示
        feedback.pushInfo("=== 処理完了統計 ===")
        feedback.pushInfo(f"  - 入力された元の地物数: {total_input_count} 件")
        feedback.pushInfo(f"  - 変更なしの地物数: {skipped_count} 件")
        feedback.pushInfo(f"  - 分割・生成でできた地物数: {total_output_count} 件")
        feedback.pushInfo(f"  - 分割・生成後の最終地物数: {total_output_count + skipped_count} 件")
        feedback.pushInfo("=====================================")

        # 作成されたレイヤのIDを出力(OUTPUT)パラメータに返す
        return {self.OUTPUT: dest_id}