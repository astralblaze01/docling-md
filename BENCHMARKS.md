# 검증 결과

## Intel Arc 140V: XPU 변환 확인

2026-11-05, Windows / Python 3.12 / PyTorch 2.15.0+xpu / Docling 2.147.0.

- 수정 전: XPU에서 `[0.1, 0.9, 0.4]` 중 `0.5`보다 큰 값을 고르면 빈 결과가 나왔고,
  `sample.pdf` 변환은 RT-DETR 결과 필터링에서 `Storage size calculation overflowed`로 실패했습니다.
- 수정 후: 캐시와 CPU 재시도 없이 Intel Arc 140V에서 샘플 **3쪽, 표 1개** 변환 성공.
  실제 레이아웃 모델 출력의 장치가 `xpu:0`임을 확인했습니다.
- 동일 프로세스에서 모델 로딩이 끝난 뒤 재변환: **0.80초** (1회, PDF → Markdown).
  설치 때 CPU 측정값 18.69초는 별도 실행 결과이며, 두 값을 직접 비교하지 않았습니다.
- `python -m unittest -v test_server test_xpu_compat`: 8개 통과.
  XPU 후처리의 점수, 레이블, 좌표가 CPU 결과와 일치하는지 포함합니다.

관련 보고: [PyTorch XPU boolean indexing #200713](https://github.com/pytorch/pytorch/issues/200713).

## 이전 CPU 전용 설치 결과

2026-11-05, Windows / Python 3.12 / PyTorch 2.15.0+cpu / Docling 2.147.0.

`sample.pdf` (3쪽) 기준이며, 다른 문서나 컴퓨터의 성능을 대표하지 않습니다.

- `python server.py bench sample.pdf --repeats 2`
- 장치별 워밍업 후 캐시 없이 변환 + Markdown 내보내기를 측정했습니다.
- CPU: 2.84초 / 2.74초, 중앙값 약 **2.79초**.
- Intel Arc 140V가 감지됐지만, XPU 빌드의 DLL을 Windows 앱 제어 정책이 차단했습니다(오류 4551).
  CPU 빌드로 전환됐으므로 GPU 속도는 측정하지 못했습니다.
- `python server.py convert sample.pdf --force`: 3쪽, 약 330 토큰 추정, 정상 변환.
- `python -m unittest -v test_server`: 5개 통과.

회귀 검증: bench 명령이 MCP 대기로 빠지지 않는지, 잘못된 명령이 종료되는지,
강제 재변환 옵션 전달, 벤치마크의 캐시 미사용, GPU 실패 시 CPU 재시도.

설치된 버전이 달라지면 측정값이 바뀔 수 있습니다.
