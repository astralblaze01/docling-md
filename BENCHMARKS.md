# 검증 결과

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
