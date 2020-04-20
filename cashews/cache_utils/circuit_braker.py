from datetime import datetime
from functools import wraps
from typing import Optional, Tuple, Type, Union

from ..backends.interface import Backend
from ..key import get_cache_key, get_cache_key_template
from ..typing import FuncArgsType

__all__ = ("circuit_breaker", "CircuitBreakerOpen")


class CircuitBreakerOpen(Exception):
    pass


def circuit_breaker(
    backend: Backend,
    errors_rate: int,
    period: int,
    ttl: int,
    exceptions: Union[Type[Exception], Tuple[Type[Exception]]] = Exception,
    key: Optional[str] = None,
    func_args: FuncArgsType = None,
    prefix: str = "circuit_breaker",
):
    """
    Circuit breaker
    :param backend: cache backend
    :param errors_rate: Errors rate in percents
    :param period: Period
    :param ttl: seconds in int or as timedelta to keep circuit breaker switched
    :param func_args: arguments that will be used in key
    :param exceptions: exceptions at which returned cache result
    :param key: custom cache key, may contain alias to args or kwargs passed to a call
    :param prefix: custom prefix for key, default "circuit_breaker"
    """
    assert 0 < errors_rate < 100

    def _decor(func):
        _key_template = key
        if key is None:
            _key_template = f"{prefix}:{get_cache_key_template(func, func_args=func_args, key=key)}"

        @wraps(func)
        async def _wrap(*args, **kwargs):
            _cache_key = get_cache_key(func, _key_template, args, kwargs, func_args)
            if await backend.is_locked(_cache_key + ":open"):
                raise CircuitBreakerOpen()
            bucket = _get_bucket_number(period, segments=100)
            total_in_bucket = await backend.incr(_cache_key + f":total:{bucket}")
            if total_in_bucket == 1:
                await backend.expire(key=_cache_key + f":total:{bucket}", timeout=period)
                await backend.set(key=_cache_key + ":fails", value=0, expire=period)
            try:
                return await func(*args, **kwargs)
            except exceptions as exc:
                fails = await backend.incr(_cache_key + ":fails")
                total = total_in_bucket + await _get_buckets_values(backend, segments=100, except_number=bucket)
                if fails * 100 / total >= errors_rate:
                    await backend.set_lock(_cache_key + ":open", True, expire=ttl)
                raise exc

        return _wrap

    return _decor


def _get_bucket_number(period: int, segments: int) -> int:
    return int((datetime.utcnow().timestamp() % period) / segments)


async def _get_buckets_values(backend: Backend, segments: int, except_number: int) -> int:
    keys = [str(key) for key in range(segments) if key != except_number]
    return sum([v for v in await backend.get_many(*keys) if v])
