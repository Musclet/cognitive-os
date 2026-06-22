# Cognitive OS — Windows 一键启动器

> Windows 快捷方式默认打开共享 Render 云端 Web/PWA。只有显式设置
> `COGNITIVE_OS_LAUNCH_MODE=local` 时才启动本地开发服务。

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `scripts/launch_web_ui.pyw` | 启动器主程序（Python，无控制台窗口） |
| **`scripts/launch_web_ui.bat`** | **推荐** — 批处理包装器（有控制台窗口，错误可见） |
| `scripts/stop_web_ui.ps1` | 停止脚本 |
| `scripts/create_web_ui_shortcut.ps1` | 创建桌面快捷方式 |
| `scripts/build_launcher_exe.ps1` | 可选 exe 打包 |
| `docs/windows-launcher.md` | 本文档 |

## 一键启动

### 方式 A：双击批处理（推荐）

直接双击 `scripts\launch_web_ui.bat`，会打开一个命令行窗口显示启动进度。

### 方式 B：桌面快捷方式

### 1. 创建桌面快捷方式

```powershell
powershell -ExecutionPolicy Bypass -File scripts\create_web_ui_shortcut.ps1
```

成功后桌面会出现 **Cognitive OS** 快捷方式。

### 2. 双击启动

双击桌面 **Cognitive OS**，启动器自动：

1. 读取 `.env` 中的启动模式。
2. 默认打开浏览器 → `https://cognitive-os.onrender.com/app/`。
3. 不启动本地后端或前端，因此电脑和手机使用同一个云端数据库。

### 3. 登录

使用 Render 环境变量 `WEB_UI_PIN` 配置的 PIN。

---

## 手动启动（不依赖快捷方式）

```bash
python scripts/launch_web_ui.pyw
```

## 本地开发模式

在本地 `.env` 中设置：

```text
COGNITIVE_OS_LAUNCH_MODE=local
```

此时启动器才会检查 Node.js、启动 8081 后端和 5173 Vite 前端。
开发结束后改回 `cloud`，避免桌面端产生一套独立于手机的本地状态。

---

## 停止服务

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_web_ui.ps1
```

该脚本只用于本地开发模式，并且只停止由启动器启动的进程。

---

## 可选：打包为 exe

如果希望生成一个单独的 `.exe` 文件（不需要 Python 环境），可以运行：

```powershell
pip install pyinstaller
powershell -ExecutionPolicy Bypass -File scripts\build_launcher_exe.ps1
```

输出：`dist/CognitiveOSLauncher.exe`

**注意：**云端模式不需要 Node.js 或 npm；本地开发模式仍需要。

---

## 访问地址

| 服务 | 地址 |
|------|------|
| 共享 Web UI | https://cognitive-os.onrender.com/app/ |
| 本地开发 UI | http://localhost:5173/app/ |
| 本地开发 API | http://localhost:8081/ |

---

## 日志

启动日志位于：

```
logs/launcher/
├── launcher.log    # 启动器日志
├── backend.log     # 后端日志
└── frontend.log    # 前端日志
```

运行时文件：

```
.runtime/
└── launcher-pids.json   # 存储由启动器启动的进程 PID
```

---

## 常见问题

### 云端首次打开较慢

Render 免费 Web 服务休眠后可能需要约一分钟唤醒，等待页面加载即可。

### 本地开发端口被占用

如果 8081 或 5173 已被占用：
- 启动器会尝试复用已有服务
- 如果端口被其他程序占用，启动器会提示

解决方法：
1. 关闭占用端口的程序
2. 或修改项目配置中的端口号

### 前端依赖未安装

```bash
cd web
npm install
```

### Python 未安装

从 https://www.python.org/downloads/ 下载安装，安装时勾选 "Add Python to PATH"。

### Node.js 未安装

从 https://nodejs.org/ 下载安装。

### 登录失败

- 云端检查 Render 的 `WEB_UI_PIN`。
- 本地开发检查 `.env` 中的 `WEB_UI_PIN` 和 8081 后端。

### 白屏 / 无法加载

- 云端确认 `https://cognitive-os.onrender.com/app/` 能打开。
- 本地开发确认 `http://localhost:5173/app/` 能打开。
- 按 F12 打开开发者工具，查看 Console 是否有错误信息
- 尝试强制刷新（Ctrl + Shift + R）

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `scripts/launch_web_ui.pyw` | 启动器主程序（Python、无额外依赖） |
| `scripts/stop_web_ui.ps1` | 停止脚本 |
| `scripts/create_web_ui_shortcut.ps1` | 创建桌面快捷方式 |
| `scripts/build_launcher_exe.ps1` | 可选 exe 打包 |
| `docs/windows-launcher.md` | 本文档 |
