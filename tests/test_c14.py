"""
C14 가상 사용자 시뮬레이션 (docs/C14_시뮬_지시.md). 시뮬은 실제 DB의 사본에서만 — 실제 DB·seed는 한 바이트도 안 바뀌어야 함.
작은 인원으로 실제로 돌려 본다 (속도 측정 없이, 따로 프로세스로).
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import config  # noqa: E402
from app.security import RateLimiter  # noqa: E402
from scripts import pack_for_replit, simulate_users  # noqa: E402

REAL_DB = BASE / "data" / "gamepick.db"
SEED_DB = BASE / "data" / "seed.db"
# 결과 비교에서 뺄 것 (실행 시간·날짜는 매번 다름)
VOLATILE = {"elapsed_seconds", "speed", "first_day", "last_day"}


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@unittest.skipUnless(REAL_DB.exists(), "data/gamepick.db가 없어 시뮬 실행 확인을 건너뜀")
class SimulationRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.before = {p: digest(p) for p in (REAL_DB, SEED_DB)}
        cls.runs = [cls.run_sim(Path(cls.tmp.name) / f"run{i}", seed=7) for i in (1, 2)]
        cls.other = cls.run_sim(Path(cls.tmp.name) / "run3", seed=8)
        cls.after = {p: digest(p) for p in (REAL_DB, SEED_DB)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def run_sim(cls, folder: Path, seed: int) -> dict:
        folder.mkdir(parents=True)
        out, metrics, report = folder / "sim.db", folder / "m.json", folder / "report.md"
        env = {k: v for k, v in os.environ.items() if k not in ("DB_PATH", "RATE_LIMITS", "PYTHONHASHSEED")}
        subprocess.run([sys.executable, str(BASE / "scripts" / "simulate_users.py"), "--users", "6", "--days", "2",
                        "--seed", str(seed), "--force", "--no-bench", "--out", str(out), "--metrics", str(metrics),
                        "--report", str(report)], check=True, capture_output=True, env=env, timeout=600)
        return {"metrics": json.loads(metrics.read_text(encoding="utf-8")), "out": out,
                "report": report.read_text(encoding="utf-8")}

    def test_real_db_and_seed_untouched(self):
        self.assertEqual(self.before, self.after)

    def test_same_seed_same_result(self):
        a, b = ({k: v for k, v in r["metrics"].items() if k not in VOLATILE} for r in self.runs)
        self.assertEqual(a, b)
        self.assertNotEqual(a, {k: v for k, v in self.other["metrics"].items() if k not in VOLATILE})

    def test_sim_users_only_in_copy(self):
        import sqlite3
        conn = sqlite3.connect(self.runs[0]["out"])
        try:
            n_sim = conn.execute("SELECT COUNT(*) FROM sim_users WHERE kind = 'user'").fetchone()[0]
            self.assertEqual(n_sim, 6)
        finally:
            conn.close()
        real = sqlite3.connect(f"{REAL_DB.as_uri()}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in real.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            real.close()
        self.assertNotIn("sim_users", tables)                                   # 실제 DB 구조는 그대로

    def test_report_says_simulation(self):
        report = self.runs[0]["report"]
        self.assertTrue(report.startswith("# 게임픽 가상 사용자 시뮬레이션 보고서"))
        self.assertIn("실제 사용자 피드백이 아닙니다", report)
        m = self.runs[0]["metrics"]
        self.assertTrue(m["simulation"])
        self.assertEqual(m["violations"]["home"], 0)                            # 비선호 위반 없음
        self.assertEqual(m["violations"]["monthly"], 0)
        self.assertEqual(m["duplicate_like_rows"], 0)                           # 같은 게임 따봉 1번 규칙


class SimSafetyTest(unittest.TestCase):
    def test_refuses_real_db_as_output(self):
        for target in (REAL_DB, SEED_DB):
            with self.assertRaises(SystemExit):
                simulate_users.prepare_db(REAL_DB, target, force=True)

    def test_sim_files_not_in_zip_or_git(self):
        patterns = pack_for_replit.read_ignore(BASE) + pack_for_replit.ALWAYS_SKIP
        for rel in ("data/sim.db", "data/sim_metrics.json"):
            self.assertTrue(pack_for_replit.ignored(rel, patterns), rel)
        if (BASE / ".git").exists():
            for rel in ("data/sim.db", "data/sim_metrics.json"):
                ok = subprocess.run(["git", "check-ignore", "-q", rel], cwd=BASE).returncode == 0
                self.assertTrue(ok, f"{rel}가 .gitignore에 안 걸림")

    def test_rate_limit_switch(self):
        limiter = RateLimiter()
        self.assertTrue(config.RATE_LIMITS_ON)                                  # 평소엔 켜져 있음
        self.assertTrue(limiter.hit("k", 1, 0.0))
        self.assertFalse(limiter.hit("k", 1, 0.1))
        with mock.patch.object(config, "RATE_LIMITS_ON", False):                 # RATE_LIMITS=off (시뮬 전용)
            self.assertTrue(all(limiter.hit("k", 1, 0.2) for _ in range(5)))


if __name__ == "__main__":
    unittest.main()
