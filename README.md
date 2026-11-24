# oscilloscope-mcp

可扩展的示波器 stdio MCP（Model Context Protocol）服务。当前内置
Keysight/Agilent DSO-X 2024A 驱动（固件 FW2.37 上验证），通过 PyVISA 访问
USB/LAN 仪器。

这是独立的 Python 项目，不依赖 BLDC 固件仓库。以下安装和测试命令从本仓库根目录运行。

## 安装

Windows 下需先安装 Keysight IO Libraries Suite（提供 USB VISA 后端
visa64.dll），再创建环境：

```bash
uv venv .venv --python 3.14
uv pip install --python .venv/Scripts/python.exe -e .
```

已用真机（FW2.37）验证 `scope_info`/`scope_get_setup`/`scope_get_screen`
与 normal 模式 CH2 波形；RAW 模式与控制类配置本轮仅经模拟测试验证。
LAN（TCPIP）连接本型号需要选件支持，本轮未验证。

## 运行测试

```bash
./.venv/Scripts/python.exe -B -m unittest discover -s tests -v
```

测试使用模拟传输和本地 MCP 子进程，不需要连接示波器。

## 注册到 Devin

```bash
devin mcp add -s local oscilloscope -- "<abs-venv-python.exe>" -m oscilloscope_mcp
```

其中 `<abs-venv-python.exe>` 替换为本仓库 `.venv` 内 Python 的绝对路径，
例如 `C:/.../oscilloscope-mcp/.venv/Scripts/python.exe`。

如果要让其他工作区也能使用这个服务，把命令中的 `-s local` 换成 `-s user`。

## 启动参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--resource` | 无 | 指定 VISA 地址；不指定时仅在唯一候选时自动连接 |
| `--visa-library` | 自动 | 64 位 Windows 且存在 `%WINDIR%/System32/visa64.dll` 时自动使用 |
| `--timeout-ms` | 15000 | VISA 超时，范围 1..120000 |
| `--output-dir` | 系统临时目录 `oscilloscope-mcp` | CSV 输出目录 |
| `--allow-control` | 关闭 | 打开后才允许写入仪器的控制类工具 |

## 工具

读取类（`readOnlyHint`）：

- `scope_list_devices`：列出候选 VISA 地址。
- `scope_info`：连接仪器，返回 `*IDN?` 身份、能力与控制开关。
- `scope_get_setup`：回读采集/时基/触发/四通道实际设置。
- `scope_get_screen`：抓取屏幕 PNG。
- `scope_read_waveform`：读取通道波形，返回均匀抽样 preview 与可选 CSV 路径。

控制类（需 `--allow-control`，调用前先做权限检查，不发起任何 I/O）：

- `scope_configure_channel` / `scope_configure_timebase` /
  `scope_configure_edge_trigger` / `scope_configure_acquisition` /
  `scope_control_acquisition`。

注意：控制调用可能使仪器现有缓存波形失效；已导出到磁盘的 CSV/图像
不受影响。`run`/`single` 之后仪器内的旧波形不再是当前采集内容。

调用示例（MCP 参数 JSON）：

```json
{"channels": [2], "mode": "normal"}
{"channel": 2, "scale_per_div": 1}
{"source": "ch2", "slope": "rising", "level": 1}
```

## 波形传输说明

- `mode="normal"` 对应 `:WAV:POIN:MODE NORM`（屏幕抽取数据），点数限
  100/250/500/1000，默认 1000；运行中也可单通道读取。
- `mode="raw"` 对应 `RAW`，点数最大 1,000,000，要求采集停止、时基 MAIN、
  采集类型 NORM/HRES；多通道传输同样要求停止。raw 实际可读深度受已显示
  窗口与固件限制，不保证满深度；仪器实际回读的点数可能少于请求值。
- 隐藏通道、PEAK 采集类型会在写入任何 `:WAV` 设置前拒绝。
- 返回的 `start_s`/`step_s` 已换算为真实秒，preview 按索引均匀抽样，
  抽样间隔大时可能遗漏窄脉冲等细节；原始码 `0` 为空洞标记，对应 value
  为 null（CSV 中为空字符串）。
- CSV 每通道原样落盘（主机不重采样），文件名带 UUID 且独占创建，
  写失败不留半成品。

## 限制

- 仅支持 DSO-X 2024A（USB vid 0x0957 / pid 0x1796 或 *IDN? 型号匹配）。
- 只读工具不做 RUN/STOP/SING/DIG/自动定标等任何采集改动。
- 屏幕传输最大 2 MB；RAW 波形最大 1M 点。

## 新增驱动

1. 在 `src/oscilloscope_mcp/drivers/` 新建 `<vendor>_<model>.py`，实现
   `model.ScopeDriver` 协议（读取、波形、可选控制），声明
   `MANUFACTURERS`/`MODEL`/`matches_identity`/`matches_resource`。
2. 在 `drivers/__init__.py` 的 `DRIVERS` 元组中追加该类。
3. USB 候选按 VISA 地址 token 匹配 vid/pid，不会对无关设备发起 *IDN?；
   显式 `--resource` 仍会验证 *IDN? 后才交给驱动。
