# Klook租车数据爬虫 - 安装说明

## 系统要求

- **操作系统**: Windows 10/11
- **Python版本**: Python 3.8 或更高版本
- **浏览器**: Google Chrome（需要安装并配置路径）
- **网络**: 本机固定代理默认监听 `http://127.0.0.1:7897`，并保证 Klook 与 DataDome 相关域名使用同一出口

## 安装步骤

### 第一步：安装Python

1. 访问 [Python官网](https://www.python.org/downloads/)
2. 下载Python 3.8或更高版本的Windows安装包
3. 运行安装程序，**重要**：勾选 "Add Python to PATH" 选项
4. 完成安装后，打开命令提示符（CMD）或PowerShell，验证安装：
   ```bash
   python --version
   ```
   应该显示Python版本号，如：`Python 3.9.x`

### 第二步：安装Google Chrome浏览器

1. 访问 [Chrome官网](https://www.google.com/chrome/)
2. 下载并安装Google Chrome浏览器
3. 确认Chrome安装路径（通常为：`C:\Program Files\Google\Chrome\Application\chrome.exe`）

### 第三步：安装项目文件

1. 将项目文件夹复制到目标位置（例如：`D:\klook_spider_aj`）
2. 确保项目文件夹包含以下文件和目录：
   - `run_klook.bat` - 运行脚本
   - `requirements.txt` - Python依赖列表
   - `src/` - 源代码目录
     - `spiderv3.py` - 主程序入口
     - `logger.py` - 日志模块
     - `data_processor.py` - 数据处理模块
     - `url_handler.py` - URL处理模块
     - `spider_core.py` - 爬虫核心模块
     - `__init__.py` - Python包初始化文件
   - `src/urls/` - URL配置文件目录
   - `src/urls/urls.json` - URL列表文件

### 第四步：运行自动安装脚本（推荐）

**方法一：使用自动安装脚本（推荐）**

1. 双击运行 `install.bat` 文件
2. 脚本会自动完成以下操作：
   - 检查Python安装
   - 升级pip
   - 安装Python依赖包
   - 安装Playwright浏览器驱动
3. 等待安装完成（可能需要几分钟）

**方法二：手动安装**

1. 打开命令提示符（CMD）或PowerShell
2. 进入项目目录：
   ```bash
   cd D:\klook_spider_aj
   ```
   （请根据实际路径修改）
3. 安装依赖包：
   ```bash
   pip install -r requirements.txt
   ```
   如果遇到网络问题，可以使用国内镜像：
   ```bash
   pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
   ```

### 第五步：安装Playwright浏览器驱动

Playwright需要单独安装浏览器驱动：

```bash
python -m playwright install chromium
```

如果下载速度慢，可以设置环境变量使用国内镜像：
```bash
set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
python -m playwright install chromium
```

### 第六步：配置Chrome路径（如需要）

如果Chrome安装在非默认路径，需要修改 `src/spider_core.py` 文件中的Chrome路径：

1. 打开 `src/spider_core.py`
2. 找到以下行（约第35行）：
   ```python
   executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe",
   ```
3. 修改为你的Chrome实际安装路径

### 第七步：运行爬虫

程序默认把整个 Playwright 浏览器上下文固定到 `http://127.0.0.1:7897`。因此 Klook
主站、租车接口、DataDome 和验证 Frame 都会进入同一个代理入口。使用其他端口时，在
启动进程前设置：

```bat
set "KLOOK_BROWSER_PROXY_SERVER=http://127.0.0.1:你的端口"
```

代理软件内部仍需把 `klook.com`、`captcha-delivery.com` 和 `datadome.co` 固定到同一个
节点，不能对其中任何域名使用 `DIRECT` 或自动切换节点。

从旧版国内站配置升级时，不要删除 `src/userdata/`。程序首次启动会保留旧资料并创建
长期运行 Profile，只定向清理国内站路由 Cookie，再设置国际站 Cookie。若最终页面、
Frame 或租车结果接口进入 `.cn`，程序会拒绝本次数据并返回失败，不会继续自动刷新。
同一时刻只能运行一个爬虫任务；Profile 已被其他任务使用时，后启动的任务会直接报错。

双击运行 `run_klook.bat` 文件，或使用命令行：

```bash
run_klook.bat
```

## 输出文件

- **CSV数据文件**: 保存在 `src/data/` 目录下
- **BM等处理后文件**: 保存在 `src/price_data/` 目录下
- **日志文件**: 保存在 `src/logs/` 目录下

## 常见问题

### 1. 提示"未找到Python"

- 确认Python已正确安装
- 确认安装时勾选了"Add Python to PATH"
- 重启命令提示符窗口
- 手动添加Python到系统PATH环境变量

### 2. Playwright安装失败

- 检查网络连接
- 使用国内镜像源（见第五步）
- 手动下载并安装Playwright浏览器驱动

### 3. Chrome浏览器无法启动

- 确认Chrome已正确安装
- 检查 `src/spider_core.py` 中的Chrome路径是否正确
- 确认Chrome版本与Playwright兼容

### 4. 依赖安装失败

- 升级pip：`python -m pip install --upgrade pip`
- 使用国内镜像源安装
- 检查网络连接

## 项目结构

```
klook_spider_aj/
├── install.bat           # 自动安装脚本（首次安装时运行）
├── run_klook.bat         # 运行脚本（日常使用）
├── requirements.txt      # Python依赖列表
├── README.md             # 本安装说明文档
├── src/                  # 源代码目录
│   ├── __init__.py       # Python包初始化文件
│   ├── spiderv3.py       # 主程序入口
│   ├── logger.py         # 日志模块
│   ├── data_processor.py # 数据处理模块
│   ├── url_handler.py    # URL处理模块
│   └── spider_core.py    # 爬虫核心模块
├── urls/                 # URL配置文件目录
│   └── urls.json         # URL列表文件
├── data/                 # CSV数据输出目录（自动创建）
├── logs/                 # 日志目录（自动创建）
└── userdata/             # 浏览器用户数据目录（自动创建）
```

## 注意事项

1. **首次运行**：首次运行时会自动创建 `data/` 和 `userdata/` 目录
2. **浏览器窗口**：程序运行时会自动打开Chrome浏览器窗口，请勿手动关闭
3. **运行时间**：根据URL数量，完整运行可能需要较长时间
4. **数据保存**：数据会实时保存到CSV文件，即使程序中断也不会丢失已保存的数据
5. **单次跑完**：程序会在一次运行内循环重试所有未完成任务（包含网络抖动/页面关闭等失败），直到全部成功或你手动中止
6. **任务状态文件**：`src/task_state.json` 仅用于本次运行过程记录，每次运行都会覆盖重建，不会在下次运行自动续跑
7. **人机验证/风控**：若出现验证页，可在 2 分钟内手动完成；超时会自动截图到 `src/logs/screenshots/` 并将任务延迟后在本次运行继续重试
8. **网络要求**：需要稳定的网络连接访问Klook.com网站（VPN能访问国外站点）

## 技术支持

如遇到问题，请检查：
1. Python版本是否符合要求
2. 所有依赖是否已正确安装
3. Chrome浏览器是否已安装
4. 网络连接是否正常
