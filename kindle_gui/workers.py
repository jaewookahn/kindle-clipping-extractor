"""QThreadPool 워커 헬퍼 — 함수를 백그라운드에서 돌리고 결과를 시그널로 돌려준다.

MacDroid MTP 마운트가 파일 읽기에서 멈출 수 있다는 걸 이 세션에서 실측으로
확인했다(DEVLOG.md 9절 — 정상적인 다른 기기가 동시에 Mass Storage 로 마운트돼
있으면 MTP 읽기가 즉시 거부당한다). 디렉터리 스캔조차 메인 스레드에서 직접
돌리지 않고 항상 여기로 태워서, 그런 상황에서도 앱 전체가 멈추지 않게 한다.
"""

from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal


class WorkerSignals(QObject):
    finished = pyqtSignal(object)   # fn 의 리턴값
    error = pyqtSignal(str)


class Worker(QRunnable):
    """fn(*args, **kwargs) 을 스레드풀에서 실행. 실패해도 UI 스레드는 안 죽는다."""

    def __init__(self, fn: Callable[..., Any], *args, **kwargs) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            self.signals.error.emit(str(e))
            return
        self.signals.finished.emit(result)


def run_in_background(
    fn: Callable[..., Any],
    *args,
    on_done: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
    **kwargs,
) -> Worker:
    """QThreadPool.globalInstance() 에 fn 을 던지고 완료/에러 콜백을 연결."""
    worker = Worker(fn, *args, **kwargs)
    if on_done:
        worker.signals.finished.connect(on_done)
    if on_error:
        worker.signals.error.connect(on_error)
    QThreadPool.globalInstance().start(worker)
    return worker
