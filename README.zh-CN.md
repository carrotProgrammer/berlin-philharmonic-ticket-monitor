# 柏林爱乐余票监控（Windows 本地版）

[English](README.md) | **简体中文**

本程序长期监控爱丁堡国际艺术节（EIF）的两场柏林爱乐演出，只在确认普通公众库存重新出现时提醒你：

- 2026年8月29日 19:00：Berliner Philharmoniker: Elgar & Tchaikovsky
- 2026年8月30日 19:30：Berliner Philharmoniker: Closing Concert

提醒方式包括 Gmail 邮件、Windows 桌面通知、明显提示音，以及用默认浏览器打开对应的官方购票页。程序**不会自动买票**，不会登录、选座、锁座、加入购物车或付款；最终购买必须由你本人完成。**Access seats、Access Pass members only、轮椅席及陪同席不会被当作普通余票。**

## 已核实的官网票务逻辑

以下信息是 2026年8月18日实际访问官网、活动页渲染结果和浏览器公开网络响应后记录的，不是猜测：

- 8月29日活动页的真实状态为禁用的 `TICKETS UNAVAILABLE`。
- 8月30日活动页显示 `ACCESS SEATS ONLY`，并写明 `Remaining seats available to Access Pass members only. Unsold seats are released 3 days before the performance.`；它不是普通余票。
- 两场活动页的官方购票跳转分别是 `https://www.eif.co.uk/book/instance/260401` 和 `https://www.eif.co.uk/book/instance/260201`，内部嵌入 Spektrix `ChooseSeats.aspx`。
- 官网前端调用两个无需登录的公开 JSON 地址：事件可售状态接口和场次锁定信息接口。普通可购场次的实测对照同时具有 `available > 0`、`instances_bookable = 1` 和可见的目标场次 `BOOK NOW` 链接。
- 目标两场当前的普通 `available` 都是 0。8月30日的锁定信息另列出要求资格的 `Wheelchair` 和 `Essential Companion` 席位，与页面的 Access-only 文案一致。
- 在本次测试网络中，直接加载 Spektrix 选座 iframe 返回 HTTP 403；程序不会绕过。常规监控使用 EIF 官网自身公开且可直接读取的 JSON，只有接口结构改变时才用 Playwright 保守检查活动页。

网站随时可能改版。只要两个接口不一致、结构不认识或页面信号不足，程序就返回 `UNKNOWN`，不会冒险误报有票。

## 1. 安装 Python

1. 打开 [Python Windows 下载页](https://www.python.org/downloads/windows/)。
2. 安装 Python 3.11 或更高版本（64 位）。
3. 安装界面务必勾选 **Add python.exe to PATH**。
4. 安装结束后不需要手工安装本项目依赖；`start_monitor.bat` 会首次自动完成。

## 2. 开启 Google 两步验证

1. 登录 [Google 账号安全页面](https://myaccount.google.com/security)。
2. 在“您登录 Google 的方式”中打开“两步验证”。
3. 按 Google 页面提示绑定手机提示、验证器或安全密钥并完成启用。

Google 官方说明：应用专用密码要求账号先启用两步验证。学校/单位管理账号、仅使用安全密钥的两步验证账号或高级保护账号可能看不到应用专用密码选项，具体限制见 [Google 官方应用专用密码帮助](https://support.google.com/accounts/answer/185833?hl=zh-Hans)。

## 3. 生成 Gmail 应用专用密码

1. 两步验证启用后，打开 [Google 应用专用密码](https://myaccount.google.com/apppasswords)。
2. 如页面要求，再次登录 Google 账号。
3. 创建一个名称，例如“EIF余票监控”。
4. Google 会显示一组 16 位应用专用密码。复制它并只保存在本机 `.env`。

这里需要的是**应用专用密码**，不是 Gmail 日常登录密码。不要把它发到聊天、截图分享或写入 `monitor.py`。Google 账号主密码改变后，旧应用专用密码可能被撤销，需要重新生成。

## 4. 首次配置和填写 `.env`

第一次双击 `start_monitor.bat` 时，它会：

1. 检查 Python；
2. 创建项目专用 `.venv`；
3. 安装 `requirements.txt`；
4. 安装 Playwright Chromium；
5. 如果没有 `.env`，复制 `.env.example` 并用记事本打开；
6. 首次配置完成后暂停，让你填写配置。本次不会直接开始监控。

也可以在 PowerShell 中执行：

```powershell
.\berlin_philharmonic_ticket_monitor\start_monitor.bat
```

`.bat` 是 Windows 批处理文件，**不要**使用 `python start_monitor.bat`；那会让 Python 把批处理命令误当成 Python 代码。三个批处理文件的控制台提示特意使用英文 ASCII，以兼容不同语言和代码页的 Windows，监控程序本身仍会显示中文状态。

在 `.env` 中填写：

```dotenv
GMAIL_ADDRESS=你的Gmail地址@gmail.com
GMAIL_APP_PASSWORD=Google生成的16位应用专用密码
RECIPIENT_EMAIL=接收通知的邮箱@example.com
```

保存并关闭记事本。`.env` 已加入 `.gitignore`，日志也不会输出密码、Cookie 或令牌。不要把 `.env` 上传或发送给别人。

## 5. 测试通知

填写 `.env` 后双击 `test_notification.bat`。它会实际执行：

- 向收件地址发送一封“测试通知”邮件；
- 弹出 Windows 桌面通知；
- 播放提示音；
- 用默认浏览器打开 8月29日官方购票页。

命令行等价命令是：

```powershell
.venv\Scripts\python.exe monitor.py --test-notification
```

如果某一渠道失败，窗口会标明，详细原因写在 `logs\monitor.log`。

## 6. 单次检查页面解析

双击 `check_once.bat`。它只检查两场各一次，打印 API 普通库存、可预订标志、Access-only 数量和判断理由，然后退出。它不会发送正式余票通知，不播放声音，也不会打开购票页面。

等价命令：

```powershell
.venv\Scripts\python.exe monitor.py --check-once --debug
```

`UNKNOWN` 表示网站暂不可访问、接口不一致或网站结构发生变化；这是一种安全状态，不会通知为有票。

## 7. 正式启动

再次双击 `start_monitor.bat`。窗口保持打开时程序会按以下节奏运行：

- 先检查 8月29日；
- 随机等待 10–15 秒；
- 再检查 8月30日；
- 将整轮随机调整到约 20–40 秒后继续。

两场状态、退避和通知锁分别维护。某场确认有票并完成通知后，该场暂停 5 分钟，另一场继续。持续有票时不重复发信；只有之后明确恢复为无票，再次放票时才重新通知。临时 `UNKNOWN` 不会让已通知的一轮库存重复发信。

监控期间电脑必须保持：

- 开机；
- 联网；
- 程序窗口未关闭；
- 未进入会停止网络或程序的睡眠/休眠状态。

Windows 的“设置 → 系统 → 电源和电池”中可调整睡眠时间。锁屏通常不影响程序，但睡眠会停止检查。

## 8. 停止程序

在监控窗口按 `Ctrl+C`。程序会停止循环、保存 `state.json` 后退出。直接关闭窗口通常也会结束进程，但优先使用 `Ctrl+C`，这样最容易确认状态已经保存。

## 9. 状态与日志

- `state.json`：分别保存两场的最后判断、检查时间、通知时间、检测到有票的时间、通知去重锁和退避状态。
- `logs\monitor.log`：记录检查时间、演出、URL、HTTP 结果、最终判断、依据、错误和是否通知。
- 日志为轮转日志：单个文件最多约 5 MB，最多保留 5 个旧文件，不会无限增大。

不要手工删除 `state.json`，否则程序会忘记已通知状态。需要调试时可运行：

```powershell
.venv\Scripts\python.exe monitor.py --debug
```

调试输出仍不会包含 Gmail 密码、Cookie 或登录令牌。

## 10. 访问保护与重试

如果遇到 HTTP 403、429、验证码、Cloudflare、排队页或明显限流提示，程序不绕过保护，而是对对应场次依次退避 1、2、5、10 分钟，并写入日志。网络断开、超时或临时服务错误会记为 `UNKNOWN` 并在后续自动重试；一个场次失败不会让另一个场次退出。

## 11. 常见问题

### 提示“未找到 Python”

重新安装 Python，并勾选 **Add python.exe to PATH**。安装后关闭旧窗口，再双击批处理文件。

### 首次依赖安装失败

确认网络正常、磁盘空间充足，然后重新双击 `start_monitor.bat`。只有全部依赖和 Chromium 都成功后才会生成 `.venv\setup_complete`，后续启动不会重复安装。

### 修改了 `requirements.txt` 或虚拟环境损坏

关闭监控，删除项目内的 `.venv` 文件夹，再双击 `start_monitor.bat` 重建。不要删除项目外的 Python。

### Gmail 显示用户名或密码错误

确认使用的是 16 位应用专用密码而不是账号登录密码；确认两步验证仍开启；如果最近改过 Google 主密码，请生成新的应用专用密码。

### 收不到邮件

先运行 `test_notification.bat`，检查垃圾邮件，并查看 `logs\monitor.log`。确认 `GMAIL_ADDRESS` 与创建应用专用密码的账号一致。

### 没有桌面通知

检查 Windows“设置 → 系统 → 通知”是否允许通知，并确认“勿扰”没有隐藏通知。提示音和浏览器仍会独立执行。

### 一直显示 `UNKNOWN`

查看日志中的 HTTP 状态。网站可能临时故障、正在排队/验证、网络断开或页面结构已改变。程序故意不把不确定情况当作有票。不要把检查间隔改为 1–5 秒，也不要试图绕过网站保护。

### Access seats 是否会触发提醒

不会。普通 `available` 必须在两个公开响应中都大于 0，且场次必须标记为可公开预订。仅有 `requiresEligibility=true` 的轮椅席、Essential Companion 或 Access Pass 席位时，结果是 `UNAVAILABLE`。

## 12. 文件说明

- `monitor.py`：监控、判断、状态、通知和退避逻辑
- `requirements.txt`：Python 依赖
- `.env.example`：安全配置模板
- `.gitignore`：排除密码、状态、日志和虚拟环境
- `start_monitor.bat`：首次安装及正式启动
- `test_notification.bat`：四种通知测试
- `check_once.bat`：单次安全检查
- `tests\test_monitor.py`：状态、去重、Access-only、断网和批处理路径测试

本工具只是个人提醒器。票务状态可能在请求之间瞬间变化；收到通知后请立即在官方页面手动确认并购买。
