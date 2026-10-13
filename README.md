# Sn_OCR AstrBot 插件

这个插件把 AstrBot 消息中的图片提交到本项目提供的 `POST /api/v1/ocr` 接口，并将识别出的电脑信息回复到当前会话。

## 安装

将整个 `astrbot_plugin_sn_ocr` 文件夹复制到 AstrBot 的 `data/plugins/` 目录，然后在 AstrBot 插件管理页重载或启用插件。插件依赖会由 AstrBot 根据目录内的 `requirements.txt` 安装。

在插件配置中填写：

- `api_base`：Sn_OCR 服务地址，例如 `http://127.0.0.1:5000`
- `api_token`：Sn_OCR 服务端的 `SN_OCR_API_TOKEN`
- 其余选项按需调整

## 使用

图片和命令可以放在同一条消息中：

```text
/snocr
```

微信等不能同时发送文字和图片时，先发送命令，再发送图片：

```text
/snocr
```

机器人会等待下一条带图片的消息。默认等待 120 秒，可在配置 `image_wait_seconds` 中修改；设为 `0` 则关闭分步识别，仍须把图片和命令一起发送。分步识别需要 AstrBot v3.4.36 及以上。

等待期间回复 `取消`、`cancel`、`退出` 或 `exit` 可以结束。超时后请重新发送 `/snocr`。只会识别接下来第一条带图片的消息，不会继续收集之后的图片。

办公电脑可以在命令中附带部门和姓名，工号不填写：

```text
/snocr 工程 小明
```

部门和姓名会写入办公电脑记录。工位电脑不使用这两个参数。默认策略是跳过重复 SN，不会覆盖已有记录。只有在命令中明确写出 `overwrite` 或 `覆盖` 时才会覆盖；需要覆盖重复 SN 时，策略词可放在前面：

```text
/snocr overwrite
```
也可以使用 `skip`、`跳过` 或 `覆盖`，例如 `/snocr overwrite 工程 小明`。命令中的策略、部门和姓名会用于随后发来的图片。

插件按批量接口一次提交这条图片消息中的图片；没有图片且未开启等待时不会发起请求。

## 更新仓库并重启

AstrBot 管理员发送：

```text
/snupdate
```

插件会在运行 AstrBot 的这台机器上执行：

```bash
git -C /opt/Sn_OCR pull --ff-only
systemctl restart sn_ocr
systemctl is-active sn_ocr
```

这要求 AstrBot 和 Sn_OCR 在同一台服务器上，并且进程用户能拉取 `/opt/Sn_OCR`、能执行 `systemctl restart sn_ocr`。目录、服务名和是否使用 `sudo -n` 可在插件配置里改。`deploy_user_ids` 留空时，所有 AstrBot 管理员都可以执行；填上用户 ID 后只允许这些账号。

该指令只更新 Sn_OCR 服务仓库并重启 `sn_ocr`，不会更新 AstrBot 插件目录本身。
