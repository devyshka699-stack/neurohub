from dataclasses import dataclass, field, asdict


@dataclass
class QCResult:
    score: int                       # 0-100
    passed: bool
    checks: dict = field(default_factory=dict)   # имя проверки -> значение
    issues: list = field(default_factory=list)   # список проблем текстом
    note: str = ""                   # краткое резюме

    def to_dict(self) -> dict:
        return asdict(self)
