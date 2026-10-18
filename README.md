# Kilo Desktop 简体中文补丁

为 **Kilo Desktop 0.1.12 Beta / macOS Apple Silicon（arm64）** 提供本地简体中文界面补丁。首版包含 **968 处文案替换**，覆盖主要设置页、聊天侧边栏、常用按钮、弹窗和提示。

这是社区制作的非官方项目，与 Kilo 官方没有从属关系。仓库提供补丁工具、替换记录和翻译词典；**不分发 Kilo 安装包、完整程序源码、汉化后的应用或用户数据**。请从 [Kilo 官方渠道](https://kilo.ai/docs/desktop/installation) 获取原应用。

## 支持范围

| 项目 | 当前支持 |
| --- | --- |
| 应用 | Kilo Desktop 0.1.12 Beta |
| 系统与架构 | macOS、Apple Silicon / arm64 |
| 内置 Electron | 44.7.0 |
| 汉化范围 | 设置页、侧边栏、常用按钮及部分提示 |
| 保留原文 | 模型和服务商品牌名称、部分第三方内容、macOS 菜单栏等 |
| 不支持 | Windows、Linux、Intel Mac、其他版本或不同构建 |

补丁同时检查版本号、CPU 架构和原版 `app.asar` 的 SHA-256。**相同版本号的其他构建也可能不兼容；校验不通过时停止，不要删除校验或强行套用。**

支持的原版资源包 SHA-256：

```text
137ad0ea53c3ed5900506d660d1dec77b4ca32ad929fdc55207c123a1acaadd1
```

## 使用前须知

修改应用会使原厂签名失效。本工具会对生成的副本进行 **ad-hoc 本地签名**，保留原有签名权限，并给 Kilo 及其辅助进程增加 `com.apple.security.cs.disable-library-validation`。这会降低 **Kilo 自身**对动态库签名的验证保护。

因此，构建需要显式传入 `--allow-local-library-signing`。如果不接受这项变化，请使用官方原版，或等待官方中文界面。工具不会关闭系统 Gatekeeper、SIP，也不会禁用 Electron 的 ASAR 完整性校验。

工具只生成独立副本，不直接覆盖“应用程序”中的 Kilo。完整原版备份在修改副本之前创建，并经过 ZIP 和资源包校验。软件更新可能覆盖汉化；新版本需要单独适配。

## 快速开始

### 1. 准备环境

- Apple Silicon Mac。
- Python **3.9 或更新版本**（运行 `python3 --version` 检查）。
- 已安装支持的 Kilo 原版；保留足够空间容纳应用副本与完整备份。
- 本工具使用 Python 标准库和 macOS 自带的 `ditto`、`codesign`，不需要 Node.js、npm 或 Python 第三方包。

下载本仓库 ZIP 并解压，或使用 Git：

```bash
git clone https://github.com/aiwenyidemaoqingnian/kilo-desktop-zh-cn.git
cd kilo-desktop-zh-cn
```

### 2. 检查原应用

```bash
python3 kilo_zh.py inspect --app /Applications/Kilo.app
```

如果显示“支持的原版，可构建中文副本”，继续下一步。如果已安装本补丁，请使用之前备份并解压出来的原版应用作为 `--app` 输入，避免重复打补丁。

### 3. 备份并生成中文副本

确认了解上面的签名变化后运行：

```bash
python3 kilo_zh.py build \
  --app /Applications/Kilo.app \
  --output "$HOME/Desktop/Kilo中文/Kilo.app" \
  --backup-dir "$HOME/Documents/Kilo原版备份" \
  --allow-local-library-signing
```

构建过程会：

1. 校验支持的原版版本、架构和资源包指纹。
2. 创建完整原版 ZIP 与 `.sha256` 校验文件。
3. 复制应用，只替换 3 个界面脚本中的指定文案。
4. 更新 ASAR 文件摘要、`Info.plist` 头部摘要，以及 Electron 框架中的内嵌摘要，保持完整性校验开启。
5. 本地签名并验证生成的应用。

如果输出目录已经存在，工具会停止。请换一个新的输出目录；工具不会自动覆盖已有副本。

### 4. 使用访达安装

1. 完全退出 Kilo。
2. 打开桌面的 `Kilo中文` 文件夹。
3. 将里面的 **Kilo.app** 拖入“应用程序”，确认替换。
4. 从“应用程序”启动 Kilo。

请在原安装位置测试。适配过程中，临时目录运行的副本曾出现内置服务连接超时，而安装到 `/Applications/Kilo.app` 后验证通过。工具不会因此修改 Kilo 的超时、重试或其他业务逻辑。

如果 macOS 报告签名或启动问题，请先使用备份恢复，再反馈具体错误。**不要通过关闭系统安全保护来强行运行。**

### 5. 验证

```bash
python3 kilo_zh.py verify --app /Applications/Kilo.app
```

命令检查资源、内嵌摘要和代码签名；界面与实际服务还需在应用中确认：

- 设置菜单和侧边栏是否为中文。
- 账户与模型列表能否加载。
- 中文设置搜索、服务商弹窗和消息输入是否正常。

首版已在一台 Apple Silicon Mac 上验证上述界面流程与内置服务连接，**未验证付费模型问答，也不代表所有插件和功能都经过测试**。

## 恢复原版

1. 完全退出 Kilo。
2. 找到构建时生成的 `Kilo-0.1.12-original-日期-标识.zip`。
3. 可在备份目录先检查文件：

   ```bash
   shasum -a 256 -c "对应的备份文件名.zip.sha256"
   ```

4. 用访达解压 ZIP，将原版 Kilo.app 拖回“应用程序”并替换。
5. 重新启动 Kilo。

完整应用备份可以恢复原版程序和签名。它**不包含账户、聊天记录或项目数据**，也不能替代这些数据自己的备份。

## 常见问题

### 为什么不直接提供汉化后的安装包？

每位使用者应从官方获取原应用，再在本地应用补丁。这也避免把其他人的账户配置、日志或完整第三方程序混进发布文件。

### 为什么只有几个设置界面没有中文？

本项目是首轮局部汉化，第三方插件动态内容、模型名称和系统菜单等可能仍是英文。欢迎提交具体界面文字；反馈截图前请隐藏账号、密钥和私人项目路径。

### 更新 Kilo 后怎么办？

更新通常会覆盖补丁。当前工具会拒绝不匹配的构建；不要把旧补丁强行应用到新版本。可以保留官方新版，等待新补丁。

### 修改翻译词典就能重新构建吗？

不能。构建使用 `patches/` 中带精确偏移和校验值的版本化记录。`translations.zh-CN.json` 用于阅读与翻译维护；修改词典后还需要重新生成、审查和测试对应补丁记录。参见 [贡献说明](CONTRIBUTING.md)。

## 开发与测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖 UTF-8 字节偏移、模板表达式保留、重叠及篡改拒绝、ASAR 重打包、校验摘要和签名权限解析。真实应用构建与 macOS 签名仍需在受支持的 Mac 上测试。

## 许可证与参考

本仓库原创工具与翻译按 [MIT 许可证](LICENSE) 发布；该许可证不适用于 Kilo 应用、上游第三方代码、商标或其他第三方资产。

- [Kilo Desktop 官方文档](https://kilo.ai/docs/desktop/overview)
- [Electron ASAR 完整性校验](https://www.electronjs.org/docs/latest/tutorial/asar-integrity)
- [Electron 44.7.0 内嵌摘要实现](https://github.com/electron/electron/blob/v44.7.0/shell/common/asar/integrity_digest.mm)
