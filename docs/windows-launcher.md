# Cognitive OS — Windows 一键启动器

> **这不是生产部署方式。**
> 这是 Windows 本地开发/自用启动器，用于快速打开 Web/PWA 图形界面。

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

1. 检查 Python 和 Node.js 是否可用
2. 检查前端依赖是否已安装（`web/node_modules`）
3. 启动后端（`python scripts/run.py`，端口 8081）
4. 启动前端（`npm run dev`，端口 5173）
5. 等待服务就绪
6. 打开浏览器 → `http://localhost:5173/`

### 3. 登录

默认本地 PIN：**123456**

---

## 手动启动（不依赖快捷方式）

```bash
python scripts/launch_web_ui.pyw
```

---

## 停止服务

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_web_ui.ps1
```

该脚本只停止由启动器启动的进程，不会影响系统中其他 Python/Node 进程。

---

## 可选：打包为 exe

如果希望生成一个单独的 `.exe` 文件（不需要 Python 环境），可以运行：

```powershell
pip install pyinstaller
powershell -ExecutionPolicy Bypass -File scripts\build_launcher_exe.ps1
```

输出：`dist/CognitiveOSLauncher.exe`

**注意：**
- PyInstaller 打包不是必需的，`.pyw` 文件直接双击就可以用
- 打包后的 exe 体积较大（~30MB），启动速度可能稍慢
- 打包后仍需 Node.js 和 npm 在前端首次启动时可用

---

## 访问地址

| 服务 | 地址 |
|------|------|
| Web UI | http://localhost:5173/ |
| API 文档 | http://localhost:8081/docs |
| FastAPI 端点 | http://localhost:8081/ |

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

### 端口被占用

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

- 确认后端已启动（`curl http://localhost:8081/api/web/auth/check`）
- 默认 PIN：`123456`
- 检查 `.env` 中是否设置了 `WEB_UI_PIN`

### 白屏 / 无法加载

- 确认前端已启动（浏览器访问 `http://localhost:5173/`）
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
