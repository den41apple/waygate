"""Type aliases используемые сразу в нескольких provisioner-модулях.

Вынесено отдельно от `steps.py` чтобы не было циркулярных импортов:
`wheel_fetch.py` нужен `ProgressEmitter`, а `steps.py` нужен `wheel_fetch`.
"""

from collections.abc import Awaitable, Callable

ProgressEmitter = Callable[[str], Awaitable[None]]
