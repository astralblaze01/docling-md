"""
이 컴퓨터의 GPU에 맞는 PyTorch를 설치한다. setup.bat 이 가상환경 python 으로 실행.

  NVIDIA GPU            → CUDA 빌드  (cuda)
  Intel GPU / 내장 GPU  → XPU 빌드   (xpu: Arc, Core Ultra 내장 Arc 등)
  그 외 / 실패          → CPU 빌드

설치 후 새 프로세스에서 실제로 GPU 연산이 되는지 확인하고, 안 되면 CPU 빌드로 되돌린다.
강제하려면:  python install_torch.py cpu | cuda | xpu
"""
import subprocess
import sys

PY = sys.executable
INDEX = "https://download.pytorch.org/whl/"
CUDA_TAGS = ["cu130", "cu128", "cu126"]  # 새 버전부터 시도

CHECK = r"""
import sys, torch
kind = sys.argv[1]
if kind == "cuda":
    ok = torch.cuda.is_available(); name = torch.cuda.get_device_name(0) if ok else ""
elif kind == "xpu":
    ok = hasattr(torch, "xpu") and torch.xpu.is_available(); name = torch.xpu.get_device_name(0) if ok else ""
else:
    ok, name = True, "cpu"
if ok and kind != "cpu":
    a = torch.randn(512, 512, device=kind)
    c = torch.nn.functional.conv2d(torch.randn(1, 3, 64, 64, device=kind), torch.randn(8, 3, 3, 3, device=kind))
    float((a @ a).sum()) + float(c.sum())
print(f"{torch.__version__}|{name}")
sys.exit(0 if ok else 1)
"""


def run(args, quiet=False):
    print("  $", " ".join(args), flush=True)
    return subprocess.run(args, stdout=subprocess.DEVNULL if quiet else None).returncode == 0


def detect_gpus():
    names = []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=30).stdout
        names = [l.strip() for l in out.splitlines() if l.strip()]
    except Exception as e:
        print("  GPU 목록 조회 실패:", e)
    has_nvidia = any("nvidia" in n.lower() for n in names)
    try:
        has_nvidia = has_nvidia or subprocess.run(["nvidia-smi"], capture_output=True).returncode == 0
    except FileNotFoundError:
        pass
    has_intel = any("intel" in n.lower() for n in names)
    return names, has_nvidia, has_intel


def works(kind):
    try:
        r = subprocess.run([PY, "-c", CHECK, kind], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print(f"  ✘ {kind} 연산 확인 시간 초과 (120초)")
        return False
    info = (r.stdout.strip().splitlines() or [""])[-1]
    if r.returncode == 0:
        print(f"  ✔ {kind} 동작 확인: torch {info.replace('|', ' / ')}")
        return True
    err = (r.stderr.strip().splitlines() or [""])[-1]
    print(f"  ✘ {kind} 사용 불가: {err or info}")
    if "4551" in r.stderr:
        print("  Windows 앱 제어 정책이 GPU DLL을 차단했습니다. CPU로 계속합니다.")
    return False


def install(tag):
    run([PY, "-m", "pip", "uninstall", "-y", "torch", "torchvision"], quiet=True)
    return run([PY, "-m", "pip", "install", "torch", "torchvision", "--index-url", INDEX + tag])


def try_kind(kind):
    if works(kind):  # 이미 맞는 빌드가 깔려 있으면 재설치 생략
        return True
    tags = CUDA_TAGS if kind == "cuda" else [kind]
    for tag in tags:
        print(f"  → PyTorch ({tag}) 설치 중... (용량이 커서 몇 분 걸릴 수 있어요)")
        if install(tag) and works(kind):
            return True
    return False


def main():
    forced = sys.argv[1].lower() if len(sys.argv) > 1 else None
    if forced not in (None, "cpu", "cuda", "xpu"):
        sys.exit("사용법: python install_torch.py [cpu|cuda|xpu]")
    names, has_nvidia, has_intel = detect_gpus()
    print("  감지된 그래픽:", ", ".join(names) or "(없음)")

    order = [forced] if forced else (["cuda"] if has_nvidia else []) + (["xpu"] if has_intel else [])
    if not order:
        print("  NVIDIA/Intel GPU가 없어 CPU로 설치합니다. (AMD GPU는 Windows PyTorch가 지원하지 않아요)")

    for kind in order:
        if kind == "cpu":
            break
        print(f"[{kind}] 시도")
        if try_kind(kind):
            print(f"\n[torch] {kind} 사용")
            return
        if kind == "xpu":
            print("  (Intel 내장 GPU는 Arc / Core Ultra 계열이 공식 지원입니다. 그 이전 Iris Xe·UHD 는 안 될 수 있고,"
                  " 최신 Intel 그래픽 드라이버가 필요합니다)")

    print("[cpu] 설치")
    if not (works("cpu") and "+cpu" in subprocess.run(
            [PY, "-c", "import torch;print(torch.__version__)"], capture_output=True, text=True).stdout):
        if not install("cpu"):
            sys.exit("PyTorch CPU 설치 실패")
    if not works("cpu"):
        sys.exit("PyTorch CPU 연산 확인 실패")
    print("\n[torch] cpu 사용")


if __name__ == "__main__":
    main()
