# Sn_OCR AstrBot 插件

这个插件把 AstrBot 消息中的图片提交到本项目提供的 `POST /api/v1/ocr` 接口，并将识别出的电脑信息回复到当前会话。

## 安装

将整个 `astrbot_plugin_sn_ocr` 文件夹复制到 AstrBot 的 `data/plugins/` 目录，然后在 AstrBot 插件管理页重载或启用插件。插件依赖会由 AstrBot 根据目录内的 `requirements.txt` 安装。

在插件配置中填写：

- `api_base`：Sn_OCR 服务地址，例如 `http://127.0.0.1:5000`
- `api_token`：Sn_OCR 服务端的 `SN_OCR_API_TOKEN`
- 其余选项按需调整

## 使用

把图片和命令放在同一条消息中：

```text
/snocr
```

默认重复 SN 会跳过。需要覆盖时使用：

```text
/snocr overwrite
```

插件按批量接口一次提交当前消息中的所有图片；没有图片时不会发起请求。
