# docling-md-mcp

PDF·PPTX·DOCX를 **Docling**으로 Markdown 변환해 캐시해 두고,
Claude에게는 **목차 → 필요한 페이지/섹션만** 넘겨서 토큰을 아끼는 로컬 MCP 서버입니다.

PDF를 채팅에 그대로 첨부하면 페이지마다 텍스트와 페이지 이미지가 같이 들어갑니다.
이 서버를 쓰면 Claude가 처음에는 **목차와 분량 정보(수백 토큰)** 만 받고,
질문에 필요한 부분만 골라 읽습니다.

## 도구

| 도구 | 하는 일 | 돌려주는 양 |
|---|---|---|
| `convert_document` | 변환 + 캐시, 목차·페이지 수·예상 토큰 반환 | 작음 |
| `get_outline` | 목차만 | 작음 |
| `search_document` | 키워드가 나온 페이지 번호 + 주변 문장 | 작음 |
| `read_pages` | 지정한 페이지 범위 본문 | 필요한 만큼 |
| `read_section` | 특정 제목의 섹션 본문 | 필요한 만큼 |
| `read_full` | 전체 본문 (짧은 문서용) | 큼 |
| `export_markdown` | 원본 옆에 `.md` 파일로 저장 | — |
| `convert_folder` | 폴더 전체 미리 변환 (백그라운드) | — |
| `list_converted` | 캐시 목록 | 작음 |

- 같은 파일은 **내용 해시로 캐시**되어 두 번째부터는 즉시 응답합니다.
- 변환이 40초 넘게 걸리면 백그라운드로 계속 돌리고 "진행 중"을 알려 줍니다.
  잠시 뒤 다시 물어보면 결과가 나옵니다 (Claude 데스크톱 도구 타임아웃 회피).
- 그림은 자리표시만 남습니다. 다이어그램이 중요한 페이지는 그 페이지만 따로 첨부하세요.

## 설치 (한 번에)

`setup.bat` 더블클릭. 가상환경(.venv) 생성 → 장치에 맞는 PyTorch → Docling·MCP 설치 →
`sample.pdf` 로 변환 테스트(모델 다운로드) → Claude 데스크톱 config 등록까지 합니다.
기존 config는 `.json.bak` 으로 백업되고 다른 MCP 서버 설정은 그대로 둡니다.
끝나면 Claude 데스크톱을 트레이까지 완전히 종료 후 다시 켜세요.

## GPU 가속

`setup.bat` 이 그래픽 장치를 확인해서 맞는 PyTorch를 설치합니다 (`install_torch.py`).

| 그래픽 | 설치되는 빌드 | 비고 |
|---|---|---|
| NVIDIA | CUDA (`cuda`) | 용량 약 2~3GB |
| Intel 내장 GPU / Arc | XPU (`xpu`) | Core Ultra(내장 Arc)·Arc 가 공식 지원. 그 이전 Iris Xe·UHD 는 안 될 수 있음. 최신 Intel 드라이버 필요 |
| AMD, 기타 | CPU | Windows용 PyTorch가 지원 안 함 |

- 설치 후 실제 GPU 연산 테스트를 해서 실패하면 자동으로 CPU 빌드로 되돌립니다.
- 서버는 `auto` 로 NVIDIA → Intel GPU → CPU 순으로 고르고, GPU에서 변환이 실패한 문서는 CPU로 다시 변환합니다.
- 설치 끝에 `sample.pdf` 로 GPU/CPU 속도를 비교해 `setup.log` 의 `[bench]` 줄에 남깁니다.
  GPU가 오히려 느리면 `DOCLING_MD_DEVICE=cpu` 로 고정하세요.
- 속도 비교는 캐시를 사용하지 않고, 장치별 워밍업 후 2회 측정한 중앙값입니다.
  GPU를 사용할 수 없으면 CPU만 측정하고 비교 생략 사유를 표시합니다.
- Windows 오류 `4551`이 나오면 앱 제어 정책이 GPU DLL을 차단한 상태입니다.
  설치는 CPU로 계속됩니다.
- 직접 측정: `.venv\Scripts\python.exe server.py bench sample.pdf --repeats 2`
- Intel Arc에서 `Storage size calculation overflowed`가 나던 문제를 우회합니다.
  모델 추론은 XPU에 두고, RT-DETR 결과 필터링만 CPU에서 처리합니다.
  기존 설치라면 Claude를 완전히 종료 후 다시 실행하면 적용됩니다.
- 장치 강제 설치: `setup.bat cpu` / `setup.bat cuda` / `setup.bat xpu`
- 지금 잡히는 장치 확인: `.venv\Scripts\python.exe server.py device`

> 다시 설치할 때는 Claude 데스크톱을 먼저 종료하세요. 서버가 PyTorch 파일을 잡고 있으면 교체가 안 됩니다.

## 수동 설치 (Windows + Anaconda)

```powershell
conda create -n docling python=3.11 -y
conda activate docling
cd <이 폴더>
pip install -r requirements.txt
```

> GPU 없는 노트북이면 CPU용 torch를 먼저 깔면 설치 용량이 훨씬 작습니다.
> `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`
> 를 `pip install -r requirements.txt` 보다 **먼저** 실행하세요.

### 1. 먼저 터미널에서 한 번 돌려 보기 (모델 다운로드 겸)

```powershell
python server.py convert "C:\Users\<사용자>\Downloads\샘플.pdf" --md
```

처음 실행할 때 레이아웃·표 인식 모델을 받아서 몇 분 걸립니다. 이후로는 오프라인으로 동작합니다.
`--md` 를 붙이면 원본 옆에 `.md` 파일도 만들어 줍니다.

### 2. Claude 데스크톱에 등록

`%APPDATA%\Claude\claude_desktop_config.json` 을 열어 `mcpServers` 에 추가합니다.
(이미 다른 서버가 있으면 그 옆에 `"docling-md": {...}` 항목만 추가)

```json
{
  "mcpServers": {
    "docling-md": {
      "command": "C:\\Users\\<사용자>\\anaconda3\\envs\\docling\\python.exe",
      "args": ["C:\\경로\\docling-md-mcp\\server.py"],
      "env": {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

`command` 의 정확한 경로는 `conda activate docling` 후 `where python` 으로 확인하세요.
저장 후 Claude 데스크톱을 **완전히 종료(트레이 아이콘까지)** 하고 다시 켭니다.

## 쓰는 법

**PDF를 채팅에 첨부하지 말고 경로를 알려 주세요.** 첨부하면 PDF가 그대로 들어가서 절약이 안 됩니다.

> `C:\Users\me\Documents\OS\week7.pdf` 에서 페이지 교체 알고리즘 부분만 정리해줘

Claude는 보통 `convert_document`로 목차를 보고 → `search_document`나 `read_section`으로 해당 부분만 읽습니다.

시험 기간 전에 과목 폴더를 통째로 미리 변환해 두면 이후엔 바로 응답합니다.

> `C:\Users\me\Documents\OS` 폴더 전부 미리 변환해줘

또는 터미널에서: `python server.py convert "C:\Users\me\Documents\OS"`

## 옵션

| 상황 | 방법 |
|---|---|
| 스캔본(글자 선택이 안 되는 PDF) | `ocr=True`. 느림. 한국어 스캔본이면 `pip install easyocr` 를 해 두세요 (설치돼 있으면 자동으로 한국어+영어 EasyOCR 사용, 없으면 Docling 기본 OCR) |
| 수식을 LaTeX로 정확히 | `formulas=True`. 느림 |
| 다시 변환 | `force=True` |

환경 변수 (config의 `env` 에 추가):

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DOCLING_MD_CACHE` | `~/.docling-md-cache` | 캐시 위치 |
| `DOCLING_MD_WAIT` | `40` | 동기로 기다리는 최대 초 |
| `DOCLING_MD_MAX_CHARS` | `30000` | 한 번에 돌려주는 최대 글자 수 |
| `DOCLING_MD_OCR_LANGS` | `ko,en` | OCR 언어 |
| `DOCLING_MD_DEVICE` | `auto` | `auto` / `cpu` / `cuda` / `xpu` |
| `DOCLING_MD_THREADS` | 코어 수/2 | CPU로 돌 때 스레드 수 |

## 문제 해결

- **도구가 안 보임**: config JSON 문법(쉼표, 역슬래시 `\\`)과 python 경로 확인.
  로그는 `%APPDATA%\Claude\logs\mcp-server-docling-md.log`.
- **이전 버전에서 "GPU / CPU 속도 비교 중"에 멈춤**: `bench` 명령이 빠져 MCP 대기 상태로 들어가던 문제입니다.
  기존 설치 창을 닫고 업데이트된 `setup.bat`을 다시 실행하세요.
- **계속 "변환 중"**: 첫 실행의 모델 다운로드일 가능성이 큽니다. 위 1번처럼 터미널에서 먼저 한 번 돌리세요.
- **본문이 거의 비어 있음**: 스캔본입니다. `ocr=True, force=True` 로 다시.
