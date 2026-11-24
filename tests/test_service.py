"""ScopeService 离线测试：fake provider/session 代替真实仪器。"""

import csv
import shutil
import struct
import tempfile
import unittest
from unittest import mock
from contextlib import contextmanager
from pathlib import Path

from oscilloscope_mcp.model import ScopeError
from oscilloscope_mcp.service import ScopeService

IDN = "KEYSIGHT TECHNOLOGIES,DSO-X 2024A,MY12345678,02.37"
USB_ADDR = "USB0::0x0957::0x1796::MY12345678::INSTR"
OTHER_ADDR = "TCPIP0::192.168.1.10::inst0::INSTR"


def make_preamble(points: int) -> str:
    return f"+1,+0,+{points},+1,2E-6,-5E-6,+1,0.02,1.25,+32768"


def make_blob(codes) -> bytes:
    payload = struct.pack(f"<{len(codes)}H", *codes)
    length = str(len(payload))
    return b"#" + str(len(length)).encode() + length.encode() + payload + b"\n"


class FakeSession:
    """按 responder 回调应答的脚本化会话；记录所有查询与写入。"""

    def __init__(self, responder=None, binary_responder=None):
        self.queries: list[str] = []
        self.writes: list[str] = []
        self.binary_queries: list[str] = []
        self.closed = False
        self._responder = responder or (lambda cmd: "0")
        self._binary_responder = binary_responder or (lambda cmd: b"#14xxxx")

    def query(self, command: str) -> str:
        self.queries.append(command)
        return self._responder(command)

    def write(self, command: str) -> None:
        self.writes.append(command)

    def query_binary(self, command: str) -> bytes:
        self.binary_queries.append(command)
        return self._binary_responder(command)


class FakeProvider:
    def __init__(self, addresses=(), sessions=None):
        self._addresses = tuple(addresses)
        self._sessions = sessions or {}
        self.opened: list[str] = []
        self.list_calls = 0

    def list_resources(self):
        self.list_calls += 1
        return self._addresses

    @contextmanager
    def open(self, resource):
        self.opened.append(resource)
        session = self._sessions[resource]
        try:
            yield session
        finally:
            session.closed = True


def base_responder(overrides=None):
    """默认应答：运行中、NORM、MAIN、EDGE/CHAN1、CH1 显示。"""
    table = {
        "*IDN?": IDN,
        ":OPER:COND?": "8",
        ":ACQ:TYPE?": "NORM",
        ":ACQ:COUN?": "8",
        ":TIM:MODE?": "MAIN",
        ":TIM:SCAL?": "2E-3",
        ":TIM:POS?": "0",
        ":TRIG:MODE?": "EDGE",
        ":TRIG:SWE?": "AUTO",
        ":TRIG:EDGE:SOUR?": "CHAN1",
        ":TRIG:EDGE:SLOP?": "POS",
        ":TRIG:EDGE:LEV?": "0.5",
        ":SYST:ERR?": "+0,\"No error\"",
        ":WAV:POIN:MODE?": "NORM",
        ":WAV:PRE?": make_preamble(4),
    }
    for ch in (1, 2, 3, 4):
        table.update(
            {
                f":CHAN{ch}:DISP?": "1",
                f":CHAN{ch}:SCAL?": "1",
                f":CHAN{ch}:OFFS?": "0",
                f":CHAN{ch}:COUP?": "DC",
                f":CHAN{ch}:PROB?": "10",
                f":CHAN{ch}:BWL?": "0",
                f":CHAN{ch}:INV?": "0",
                f":CHAN{ch}:UNIT?": "VOLT",
            }
        )
    table.update(overrides or {})

    def respond(cmd):
        if cmd in table:
            return table[cmd]
        raise AssertionError(f"未预期查询: {cmd}")

    return respond


def make_service(tc=None, session=None, addresses=None, **kwargs):
    session = session or FakeSession(base_responder())
    addresses = addresses if addresses is not None else [USB_ADDR]
    provider = FakeProvider(addresses, {USB_ADDR: session})
    if "output_dir" not in kwargs:
        outdir = Path(tempfile.mkdtemp())
        if tc is not None:
            tc.addCleanup(shutil.rmtree, outdir, True)
        kwargs["output_dir"] = outdir
    return ScopeService(provider, **kwargs), provider, session


class ConnectTest(unittest.TestCase):
    def test_unique_candidate_auto_connects_and_closes(self):
        service, provider, session = make_service(self)
        info = service.get_info()
        self.assertEqual(info["identity"]["model"], "DSO-X 2024A")
        self.assertEqual(info["resource"], USB_ADDR)
        self.assertFalse(info["control_enabled"])
        self.assertTrue(session.closed)

    def test_ambiguous_candidates_require_resource(self):
        other = USB_ADDR.replace("MY12345678", "OTHER")
        provider = FakeProvider([USB_ADDR, other], {USB_ADDR: FakeSession()})
        service = ScopeService(provider, output_dir=Path(tempfile.mkdtemp()))
        with self.assertRaises(ScopeError):
            service.get_info()
        self.assertEqual(provider.opened, [])

    def test_explicit_resource_used_and_idn_verified(self):
        service, provider, _ = make_service(self,
            resource=USB_ADDR, addresses=[OTHER_ADDR, USB_ADDR]
        )
        service.get_info()
        self.assertEqual(provider.opened, [USB_ADDR])

    def test_unsupported_idn_rejected(self):
        session = FakeSession(base_responder({"*IDN?": "TEKTRONIX,TDS2000,1,1"}))
        service, _, _ = make_service(self, session=session)
        with self.assertRaises(ScopeError):
            service.get_info()
        self.assertTrue(session.closed)
        self.assertEqual(session.queries, ["*IDN?"])

    def test_list_devices_marks_configured_resource(self):
        service, _, _ = make_service(self, resource=USB_ADDR)
        self.assertEqual(
            service.list_devices(),
            [{"resource": USB_ADDR, "configured": True}],
        )
        other = "TCPIP0::192.168.1.10::inst0::INSTR"
        service, _, _ = make_service(self, resource=other)
        self.assertEqual(
            service.list_devices(),
            [
                {"resource": USB_ADDR, "configured": False},
                {"resource": other, "configured": True},
            ],
        )

    def test_no_candidate_raises(self):
        service, _, _ = make_service(self, addresses=[])
        with self.assertRaises(ScopeError):
            service.get_info()


class WaveformTest(unittest.TestCase):
    def blob(self):
        return make_blob([32768, 32768, 0, 32868])

    def test_raw_rejected_before_writes_while_running(self):
        session = FakeSession(base_responder())
        service, _, _ = make_service(self, session=session)
        with self.assertRaises(ScopeError):
            service.read_waveform([1], mode="raw", preview_points=0, save_csv=False)
        self.assertEqual(session.writes, [])

    def test_raw_rejected_for_non_norm_acquisition(self):
        session = FakeSession(
            base_responder({":OPER:COND?": "0", ":ACQ:TYPE?": "AVER"})
        )
        service, _, _ = make_service(self, session=session)
        with self.assertRaises(ScopeError):
            service.read_waveform([1], mode="raw", preview_points=0, save_csv=False)
        self.assertEqual(session.writes, [])

    def test_peak_rejected_even_in_normal_mode(self):
        session = FakeSession(base_responder({":ACQ:TYPE?": "PEAK"}))
        service, _, _ = make_service(self, session=session)
        with self.assertRaises(ScopeError):
            service.read_waveform([1], preview_points=0, save_csv=False)
        self.assertEqual(session.writes, [])

    def test_multi_channel_requires_stopped(self):
        session = FakeSession(base_responder())
        service, _, _ = make_service(self, session=session)
        with self.assertRaises(ScopeError):
            service.read_waveform([1, 2], preview_points=0, save_csv=False)
        self.assertEqual(session.writes, [])

    def test_hidden_channel_rejected(self):
        session = FakeSession(base_responder({":CHAN2:DISP?": "0"}))
        service, _, _ = make_service(self, session=session)
        with self.assertRaises(ScopeError):
            service.read_waveform([2], preview_points=0, save_csv=False)
        self.assertEqual(session.writes, [])

    def test_normal_waveform_preview_csv_and_write_order(self):
        session = FakeSession(
            base_responder(), binary_responder=lambda cmd: self.blob()
        )
        outdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outdir, True)
        service, _, _ = make_service(self, session=session, output_dir=outdir)
        result = service.read_waveform([1], preview_points=3, save_csv=True)

        self.assertTrue(result["acquisition_running"])
        self.assertEqual(result["mode"], "normal")
        entry = result["channels"][0]
        self.assertEqual((entry["channel"], entry["unit"], entry["sample_count"]),
                         (1, "V", 4))
        self.assertEqual([s["index"] for s in entry["preview"]], [0, 1, 3])
        self.assertAlmostEqual(entry["preview"][0]["time_s"], -7e-6, places=14)
        self.assertAlmostEqual(entry["preview"][0]["value"], 1.25)
        with open(entry["csv_path"], newline="") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0], ["index", "time_s", "value", "unit", "raw_code"])
        self.assertEqual(rows[3][2], "")  # 缺失点写空值
        self.assertEqual(rows[3][4], "0")

        expected_writes = [
            ":WAV:SOUR CHAN1",
            ":WAV:FORM WORD",
            ":WAV:BYT LSBF",
            ":WAV:UNS 1",
            ":WAV:POIN:MODE NORM",
            ":WAV:POIN 1000",
        ]
        self.assertEqual(session.writes, expected_writes)
        # 读取路径不允许任何采集控制写入。
        for cmd in session.writes:
            self.assertNotIn(cmd.split()[-1], (":RUN", ":STOP", ":SING"))
        self.assertIn(":WAV:DATA?", session.binary_queries)

    def test_raw_stopped_success_uses_raw_mode_and_max_points(self):
        session = FakeSession(
            base_responder({":OPER:COND?": "0", ":WAV:POIN:MODE?": "RAW"}),
            binary_responder=lambda cmd: self.blob(),
        )
        service, _, _ = make_service(self, session=session)
        result = service.read_waveform(
            [1], mode="raw", preview_points=0, save_csv=False
        )
        self.assertFalse(result["acquisition_running"])
        self.assertEqual(result["points_requested"], 1_000_000)
        self.assertEqual(
            session.writes,
            [
                ":WAV:SOUR CHAN1",
                ":WAV:FORM WORD",
                ":WAV:BYT LSBF",
                ":WAV:UNS 1",
                ":WAV:POIN:MODE RAW",
                ":WAV:POIN 1000000",
            ],
        )

    def test_raw_aborts_when_acquisition_resumed(self):
        calls = {"n": 0}
        base = base_responder({":WAV:POIN:MODE?": "RAW"})

        def responder(cmd):
            if cmd == ":OPER:COND?":
                calls["n"] += 1
                return "0" if calls["n"] == 1 else "8"
            return base(cmd)

        outdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outdir, True)
        session = FakeSession(responder, binary_responder=lambda cmd: self.blob())
        service, _, _ = make_service(self, session=session, output_dir=outdir)
        with self.assertRaises(ScopeError):
            service.read_waveform([1], mode="raw")
        self.assertEqual(list(outdir.iterdir()), [])  # 失败不留导出文件

    def test_csv_name_conflict_keeps_existing_file(self):
        session = FakeSession(
            base_responder(), binary_responder=lambda cmd: self.blob()
        )
        service, _, _ = make_service(self, session=session)
        victim = service._output_dir / "waveform-ch1-deadbeef.csv"
        victim.write_text("已有内容", encoding="utf-8")
        with mock.patch(
            "oscilloscope_mcp.service.uuid.uuid4"
        ) as fake_uuid, self.assertRaises(FileExistsError):
            fake_uuid.return_value.hex = "deadbeef"
            service.read_waveform([1], preview_points=0)
        self.assertEqual(victim.read_text(encoding="utf-8"), "已有内容")

    def test_preview_and_csv_edge_cases(self):
        session = FakeSession(
            base_responder(), binary_responder=lambda cmd: self.blob()
        )
        service, _, _ = make_service(self, session=session)
        result = service.read_waveform(
            [1], preview_points=0, save_csv=False
        )
        self.assertEqual(result["channels"][0]["preview"], [])
        self.assertIsNone(result["channels"][0]["csv_path"])
        with self.assertRaises(ScopeError):
            service.read_waveform([1], preview_points=300)


class ControlTest(unittest.TestCase):
    def test_control_blocked_before_any_io(self):
        session = FakeSession(base_responder())
        service, provider, _ = make_service(self, session=session)
        with self.assertRaises(ScopeError):
            service.configure_channel(1, enabled=True)
        with self.assertRaises(ScopeError):
            service.control_acquisition("stop")
        self.assertEqual(provider.opened, [])
        self.assertEqual(session.writes, [])

    def test_configure_channel_write_order(self):
        session = FakeSession(base_responder())
        service, _, _ = make_service(self, session=session, allow_control=True)
        result = service.configure_channel(
            2,
            enabled=True,
            scale_per_div=0.5,
            offset=-1.25,
            coupling="ac",
            probe_attenuation=1,
            bandwidth_limit=True,
            invert=False,
        )
        self.assertEqual(
            session.writes,
            [
                ":CHAN2:COUP AC",
                ":CHAN2:PROB 1",
                ":CHAN2:SCAL 0.5",
                ":CHAN2:OFFS -1.25",
                ":CHAN2:BWL 1",
                ":CHAN2:INV 0",
                ":CHAN2:DISP 1",
            ],
        )
        self.assertEqual(result["channel"], 2)

    def test_instrument_fault_short_circuits_writes(self):
        calls = {"n": 0}

        def responder(cmd):
            if cmd == ":SYST:ERR?":
                calls["n"] += 1
                # 写入前检查正常，写完 SCAL 后的检查报错。
                return "+0,\"No error\"" if calls["n"] < 2 else '-113,"Undefined header"'
            return base_responder()(cmd)

        session = FakeSession(responder)
        service, _, _ = make_service(self, session=session, allow_control=True)
        with self.assertRaises(ScopeError):
            service.configure_channel(1, scale_per_div=1, offset=0)
        self.assertEqual(session.writes, [":CHAN1:SCAL 1"])

    def test_validation_happens_before_first_write(self):
        session = FakeSession(base_responder())
        service, _, _ = make_service(self, session=session, allow_control=True)
        with self.assertRaises(ScopeError):
            service.configure_channel(5, enabled=True)
        with self.assertRaises(ScopeError):
            service.configure_channel(1, scale_per_div=-1)
        self.assertEqual(session.writes, [])

    def test_average_count_rejected_for_non_average_mode(self):
        session = FakeSession(base_responder())
        service, _, _ = make_service(self, session=session, allow_control=True)
        with self.assertRaises(ScopeError):
            service.configure_acquisition(mode="normal", average_count=8)
        self.assertEqual(session.writes, [])

    def test_average_count_with_average_mode_writes_type_then_count(self):
        session = FakeSession(
            base_responder({":ACQ:TYPE?": "AVER", ":ACQ:COUN?": "16"})
        )
        service, _, _ = make_service(self, session=session, allow_control=True)
        result = service.configure_acquisition(mode="average", average_count=16)
        self.assertEqual(
            session.writes, [":ACQ:TYPE AVER", ":ACQ:COUN 16"]
        )
        self.assertEqual(result["type"], "AVER")
        self.assertEqual(result["average_count"], 16)


class SetupTest(unittest.TestCase):
    def test_setup_readback(self):
        service, _, session = make_service(self)
        setup = service.get_setup()
        self.assertEqual(setup["acquisition"]["type"], "NORM")
        self.assertTrue(setup["acquisition"]["running"])
        self.assertEqual(setup["trigger"]["edge"]["source"], "CHAN1")
        self.assertEqual(len(setup["channels"]), 4)
        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()
