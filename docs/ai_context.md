# プロジェクト概要

VRChat のワールド `Idle Home` をデスクトップモードで周回するための Windows 向け自動化ツールを作っている。

ツールは `VRChat` ウィンドウを前面に置き、キーボード・マウス入力と画像認識を組み合わせて以下を自動化する。

- 剣の取得
- 戦闘位置への移動
- avatar auto-attack の ON/OFF
- Astral Ascension の実行
- 次 cycle への復帰

現在は Python スクリプトと Tkinter GUI で構成されている。配布用には `build_release.bat` で PyInstaller の `onedir` 形式 EXE を作成する。

主なファイルは以下。

- `idle_home_bot.py`: 自動化本体、画像認識、failure capture、ntfy 通知
- `idle_home_gui.py`: GUI、設定編集、実行ボタン、LAN 内ステータスページ
- `idle_home_config.json`: main PC 用 config
- `idle_home_config_4790K.json`: 4790K PC 用 config
- `idle_home_config_3950X.json`: 3950X PC 用 config
- `templates/`: 画像認識テンプレート
- `failure_captures/`: 失敗時スクリーンショット・ログ保存先
- `launch_gui*.bat`: 各 config 用 GUI 起動 bat

重要な進捗や設計変更があった場合は、この `docs/ai_context.md` を随時更新する。

# 現在の目的

直近の目的は、複数 PC で `Idle Home` 周回を長時間放置できるレベルまで安定化しつつ、離席中でもスマホから状態確認できる運用にすること。

具体的には以下を目指している。

- main PC / 4790K PC / 3950X PC それぞれの config を安定化する
- GUI から config を選んで起動できるようにする
- iPhone から LAN 内ステータスページで稼働状況を確認できるようにする
- `ntfy` で BotError 停止時に通知を受け取れるようにする
- 安定している状態を release tag で固定し、いつでも戻れるようにする

# 現在の進捗

完了済みの主な内容。

- 基本周回フローを実装済み
- `pickup_sword` は画像認識で剣本体を探してクリックする方式
- 剣取得時の視点補正後、補正分だけ視点を戻す処理を実装済み
- `ascend` の `▶` ボタンは画像認識でクリックする方式
- `after_ascend` で Ascension UI 残留を検出し、必要に応じて再クリックする処理を実装済み
- 失敗時に `failure_captures/` へ screenshot / metadata / cycle log / recent log / sequence snapshot を保存する処理を実装済み
- GUI で主要パラメータを編集可能
- GUI で `Open Config...` により任意の JSON config を選択可能
- `launch_gui_4790K.bat` と `launch_gui_3950X.bat` を追加済み
- GUI 上に `Current Cycle` を表示済み
- GUI 起動中に LAN 内ステータスページを公開済み
- ステータスページで `Running/Stopped`、current cycle、current sequence、最新ログ、最新 error、最新 failure image を確認可能
- `ntfy` 通知を実装済み
- GUI に `Test Notification` ボタンを追加済み
- `idle_home_config.json` と `idle_home_config_3950X.json` には `ntfy Topic: idle-home-main` を設定済み
- v0.0.5 release state を作成済み
- v0.0.6 release state を作成済み

リリース履歴の現状。

- `v0.0.1`: GUI 初期リリース
- `v0.0.2`: failure artifact / after_ascend 周りを強化
- `v0.0.3`: main PC / 4790K PC の安定寄り状態を固定
- `v0.0.4`: 3950X PC 対応と `Next Click Count` などを反映
- `v0.0.5`: LAN 内ステータスページと `ntfy` 通知を追加
- `v0.0.6`: recovery の最大3回再試行とGUI/ビルド表記更新

# 作業ログ（今回）

今回の作業では、現在のプロジェクト状況を初見でも追えるようにするため、この `docs/ai_context.md` を新規作成した。

直近の大きな作業内容は以下。

- LAN 内ステータスWebページを GUI に実装
- `http://<PC-IP>:8787/` で iPhone などから状態確認できるようにした
- ステータスページに最新 failure screenshot を表示
- `ntfy` 通知を実装
- GUI に `Notifications` タブを追加
- GUI に `Test Notification` ボタンを追加
- iPhone 側では `ntfy.sh` 利用時に `Use another server` を OFF にする運用を確認
- v0.0.5 用 commit / tag を作成
- `Idle Home.code-workspace` を Git 管理対象に追加
- 停止時に自動復帰する方針を整理した
- 復帰処理は `BotError` 発生時に failure artifact を保存してから、専用 recovery sequence を実行する方針
- 復帰処理はリスポーン、視点リセット、復帰用 ascend、`after_ascend` 確認の順に実行する方針
- ESC メニューの `リスポーン` ボタンは押下後に確認ダイアログなしで即リスポーンすることを確認
- リスポーン後は 1 秒程度待てば初期地点へ戻る想定
- 復帰用 ascend の移動検証用に `recover_move_to_ascend_board` sequence を追加
- `recover_move_to_ascend_board` は実機検証結果に合わせて `W 0.40s -> D 2.81s` に調整
- 視点リセット検証用に `recover_reset_view` sequence を追加
- `recover_reset_view` は実機検証結果に合わせて `dy=-2500 -> dy=980` に調整
- 復帰用 ascend 単体テスト用に `recover_to_ascend` sequence を追加
- ESC メニュー画像から `templates/respawn_button.png` を作成
- リスポーン単体テスト用に `recover_respawn` sequence を追加
- `recover_respawn` の `vision_center_click` は固定 UI には不適だったため、固定座標 `x=750, y=715` の `left_click` へ変更
- `recover_respawn` は複数回の実機テストでリスポーン成功を確認
- リスポーン、視点リセット、復帰用 ascend を連結した `recover_from_failure` sequence を追加
- `BotError` 発生時に `recover_from_failure -> after_ascend` を1回実行し、成功したら次 cycle へ戻る自動復帰処理を追加
- `recovery.enabled=true` を main / 4790K / 3950X の各 config に追加
- ESC メニューが既に開いている状態でも壊れにくいよう、`respawn_from_escape_menu` action を追加
- `respawn_from_escape_menu` はリスポーンボタンが見えていればそのままクリックし、見えていなければ `ESC` で開いてからクリックする
- LAN 内ステータスページに `Started At` / `Last Recovery` / `Stopped At` / `Summary Events` を追加
- GUI 起動フォルダに `status_summary.log` を作成し、START / RECOVERY START / RECOVERY SUCCESS / STOP を要約ログとして追記する
- recovery は `max_attempts` を設定可能にし、main / 4790K / 3950X の各 config は最大3回まで復帰を試行する
- GUI の表示バージョン、release build のデフォルトバージョン、README のビルド例を `v0.0.6` に更新
- GUI の recovery summary 正規表現を `max_attempts` / `on attempt x/y` 付きログに対応

# MCP操作ログ

今回の作業では MCP 操作は実施していない。

特に `unity-synaptic` / Synaptic AI Pro / Unity HTTP MCP による操作は行っていない。  
このプロジェクトは現時点では Unity プロジェクトではなく、Windows デスクトップ入力自動化ツールとして作業している。

今後 `unity-synaptic` を使う作業が発生した場合は、以下をこのセクションへ追記する。

- 実行した MCP tool 名
- 対象 scene / object / asset
- 変更内容
- 検証結果

# 未完了タスク

未完了または継続調整中のもの。

- 3950X PC の長時間安定化
- 4790K PC の長時間安定化
- main PC の長時間安定化の継続確認
- `after_ascend` の誤判定・早抜けが再発しないかの監視
- `pickup_sword` の取得確認が甘すぎる / 厳しすぎるケースの継続調整
- `ascend` の `▶` 画像認識が立ち位置ズレで失敗するケースの継続調整
- v0.0.6 の GitHub Release 作成と zip アップロード
- EXE ビルド後の status page / ntfy 通知の実機確認
- `ntfy` topic の運用ルール整理
- ESC メニューの `リスポーン` ボタンテンプレート作成
- 復帰用 ascend sequence の実機調整
- `recover_move_to_ascend_board` の `W` / `D` 秒数調整
- `recover_reset_view` の上下マウス移動量調整
- `recover_to_ascend` の単体テスト
- `recover_respawn` の単体テスト
- `recover_from_failure` の一連動作テスト
- 自動復帰が実際の `BotError` 停止時に正しく動くかの長時間検証
- LAN 内ステータスページの要約ログが実機で期待通り更新されるか確認
- `status_summary.log` が各 PC の config フォルダに保存されるか確認

# 次にやること

次回すぐ着手する作業。

1. v0.0.6 を GitHub へ push する

```powershell
git push origin main
git push origin v0.0.6
```

2. v0.0.6 の release zip を作成する

```powershell
.\build_release.bat 0.0.6
```

3. GitHub Release に以下をアップロードする

```text
release\IdleHomeBot-v0.0.6.zip
```

4. 各 PC で最新を取得する

```powershell
git pull
```

5. GUI を起動して status page と ntfy を確認する

```powershell
.\launch_gui.bat
.\launch_gui_3950X.bat
.\launch_gui_4790K.bat
```

6. iPhone でステータスページを開く

```text
http://<PC-IP>:8787/
```

7. GUI の `Test Notification` で `ntfy` 通知を確認する

8. 長時間放置テストを再開する

9. 停止時の自動復帰処理を実装する

- `recover_respawn` を任意位置から実行し、ESC メニューを開いてリスポーンできるか確認する
- `recover_reset_view` を単体テストし、上方向限界移動量と正面戻し量を調整する
- `recover_move_to_ascend_board` を初期地点から実行し、`W 0.5s -> D 3.0s` の移動量を実機で調整する
- `recover_to_ascend` sequence を追加して単体テストする
- `recover_from_failure` を任意位置から実行し、リスポーンから Ascend 完了まで通るか確認する
- recovery 成功時は次 cycle へ戻ることを長時間テストで確認する
- recovery 失敗時だけ停止通知されることを確認する
- LAN 内ステータスページで `Started At` / `Last Recovery` / `Stopped At` / `Summary Events` を確認する

10. 停止した場合は `failure_captures/` の最新一式を見て原因を分類する

- `pickup_sword` 取得失敗
- `pickup_sword` 取得確認の誤判定
- `ascend` 立ち位置ズレ
- `after_ascend` UI 残留
- menu / Launch Pad 残留
- その他

11. 重要な進捗・設計変更・安定化結果が出たら、この `docs/ai_context.md` を更新する

# 注意点・制約

- この bot は Windows の実マウス・キーボード入力を使うため、動作中は基本的に PC を占有する
- VRChat は前面にあり、フォーカスされている必要がある
- 基本解像度は `1600x900`
- マウス感度、FPS、アバター視点、PC 負荷で移動量が変わるため、PC ごとの config 調整が必要
- `idle_home_config.json` は main PC 用
- `idle_home_config_4790K.json` は 4790K PC 用
- `idle_home_config_3950X.json` は 3950X PC 用
- `failure_captures/` は原因調査の主情報源
- failure screenshot だけでなく `.log`、`.recent.log`、`_snap*.png` も見る
- `git pull` 時に config のローカル変更があると merge が止まる
- Codex 環境から GitHub への `git push` はネットワーク制限で失敗するため、push はユーザー側で実行する
- `build/`、`dist/`、`release/` は生成物であり、基本的に Git 管理対象ではない
- `ntfy.sh` を使う場合はインターネット接続が必要
- iPhone の `ntfy` アプリで公開 `ntfy.sh` を使う場合、`Use another server` は OFF にする
- LAN 内ステータスページは GUI 起動中のみ有効
- CLI で `idle_home_bot.py run` した場合、現状ではステータスページは出ない
- Windows Firewall が status page の LAN アクセスを止める場合がある
- recovery は無限ループさせない。まずは 1 cycle あたり 1 回だけ試行する
- recovery 成功時は通知を出しすぎないようにし、ログとステータスページで確認できる形を優先する
- recovery 用 ascend は通常 `ascend` と前提位置が違うため、通常 sequence をそのまま流用しない
- リスポーン後の視界 pitch はリスポーン前の視界に影響されるため、recovery では視点リセットを入れる前提にする
- ESC メニューは画面固定 UI なので、`vision_center_click` で中央寄せしない
- リスポーンは `respawn_from_escape_menu` を使う。単純に `ESC -> 固定クリック` だけだと、メニューが既に開いている状態で ESC により閉じてしまうリスクがある
- 重要な進捗があった場合は、この `docs/ai_context.md` を随時更新する
