"""
Replit에 올릴 파일 묶기 (PROJECT.md 4.11 C10, docs/C10_배포_지시.md 1단계 6)).

dist/gamepick_replit.zip 을 만든다.
- 뺌: .gitignore에 걸린 것(.env, DB, 아이콘, 로그, .idea, __pycache__ …) + legacy/, dist/, .git/, .claude/, docs/
  (docs/는 실행에 필요 없고, 한글 파일 이름 때문에 Replit에서 zip을 풀 때 invalid UTF-8로 배포가 실패했음 — C11-a)
- zip 안 파일 이름은 ASCII만. 아니면 묶기 전에 NonAsciiName 오류로 멈춤
- 넣음: data/seed.db (.gitignore의 data/*.db에 걸리지만 첫 배포의 시작용 DB라 예외)
- .env 같은 비밀값 파일은 어떤 경우에도 넣지 않음
묶은 파일 목록과 크기를 출력한다.

실행: python scripts/pack_for_replit.py        (먼저 python scripts/make_seed_db.py)
"""
import argparse
import fnmatch
import sys
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUT = BASE_DIR / "dist" / "gamepick_replit.zip"
ALWAYS_SKIP = ["legacy/", "dist/", ".git/", ".claude/", "docs/"]
FORCE_INCLUDE = {"data/seed.db"}
NEVER_INCLUDE = {".env"}  # 비밀값 (.gitignore에서 빠져도 넣지 않음)


class NonAsciiName(ValueError):
    """zip에 넣을 파일 이름에 ASCII가 아닌 글자가 있음 (Replit에서 풀 때 깨짐)"""


def read_ignore(base: Path) -> list[str]:
    path = base / ".gitignore"
    if not path.exists():
        return []
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def ignored(rel: str, patterns: list[str]) -> bool:
    """간단한 .gitignore 규칙: 'dir/' = 그 폴더 전체, '/'가 있으면 최상위 기준 경로, 없으면 어느 단계 이름이든"""
    parts = rel.split("/")
    for raw in patterns:
        pattern = raw.lstrip("/")
        if pattern.endswith("/"):
            name = pattern.rstrip("/")
            if "/" in name:
                if rel.startswith(name + "/"):
                    return True
            elif any(fnmatch.fnmatch(p, name) for p in parts[:-1]):
                return True
        elif "/" in pattern:
            if fnmatch.fnmatch(rel, pattern) or rel.startswith(pattern + "/"):
                return True
        elif any(fnmatch.fnmatch(p, pattern) for p in parts):
            return True
    return False


def collect(base: Path = BASE_DIR) -> list[Path]:
    patterns = read_ignore(base) + ALWAYS_SKIP
    files = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        if rel in NEVER_INCLUDE or path.name == ".env":
            continue
        if rel in FORCE_INCLUDE or not ignored(rel, patterns):
            files.append(path)
    return files


def pack(base: Path = BASE_DIR, out: Path = OUT) -> list[tuple[str, int]]:
    seed = base / "data" / "seed.db"
    if not seed.exists():
        raise FileNotFoundError("data/seed.db가 없습니다. 먼저 python scripts/make_seed_db.py 를 실행하세요.")
    files = collect(base)
    bad = [path.relative_to(base).as_posix() for path in files if not path.relative_to(base).as_posix().isascii()]
    if bad:  # zip을 만들기 전에 멈춤 (반쯤 만든 zip이 남지 않게)
        raise NonAsciiName("파일 이름이 ASCII가 아니라 Replit에서 풀 때 깨집니다: " + ", ".join(bad))
    out.parent.mkdir(parents=True, exist_ok=True)
    listed = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            rel = path.relative_to(base).as_posix()
            zf.write(path, rel)
            listed.append((rel, path.stat().st_size))
    return listed


def main(argv=None):
    argparse.ArgumentParser(description="Replit에 올릴 zip 만들기").parse_args(argv)
    listed = pack()
    for rel, size in listed:
        print(f"{size / 1024:>9.1f} KB  {rel}")
    total = sum(size for _, size in listed)
    print(f"\n{len(listed)}개 파일, 원본 {total / 1024 / 1024:.1f} MB → {OUT} ({OUT.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
