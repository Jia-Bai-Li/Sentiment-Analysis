import asyncio
from dataclasses import dataclass
from functools import wraps
from loguru import logger

@dataclass(slots=True)
class RetryConfig:
    """
    重试类属性配置
    """
    # 最大重试次数
    max_retry: int = 3
    # 初始延迟时间，单位为秒
    init_delay: float = 1.0
    # 最大延迟时间，单位为秒
    max_delay: float = 60.0
    # 延迟倍数因子
    backoff_factor: float = 2.0

    # 根据重试次数计算延迟时间
    def _computer_retry_delay(self, attempt: int) -> float:
        # 计算延迟时间
        return min(self.init_delay * (self.backoff_factor ** attempt), self.max_delay)

    # 检查哪些异常需要重试：
    @staticmethod
    def _no_retryable(exec: Exception) -> bool:
        # 获取异常状态码
        status_code = getattr(exec, "status_code", None)
        if status_code is None:
            # 如果exec中没有状态码，则需要去响应中获取
            status_code = getattr(getattr(exec, "response", None), "status_code", None)
        # 返回是否需要重试 返回值True-不需要重试，False-需要重试
        return isinstance(status_code, int) and 400 <= status_code < 500 and status_code != 429

    # 获取延迟
    def get_retry_delay(self, exec: Exception, attempt: int, func_name: str) -> float | None:
        # 1. 不需要重试.
        if self._no_retryable(exec) or attempt >= self.max_retry - 1:
            return None
        # 2. 计算重试时间
        current_try = attempt + 1
        next_try = current_try + 1
        # 3. 获取到延迟时间
        delay = self._computer_retry_delay(attempt)
        logger.warning(f"函数{func_name} 第 {current_try} 次尝试失败：{exec}")
        logger.warning(f"将在{delay:.1f} 秒后进行第 {next_try} 次尝试....")
        # 4. 返回数据
        return delay

# 定义装饰器
def with_retry(func):
    # 1. 判断函数是否属于协程函数
    if not asyncio.iscoroutinefunction(func):
        raise TypeError(
            "装饰器只能修饰async函数."
        )

    # 2. 获取目标方法参数
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # 3. 循环遍历
        for attempt in range(retry_config.max_retry):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                # 4.计算延迟时间
                delay = retry_config.get_retry_delay(e, attempt, func.__name__)
                if delay is None:
                    return ValueError(f"{func.__name__}函数不需要重试，异常原因：{str(e)}")
                # 5.设置延迟
                await asyncio.sleep(delay)


    return wrapper


# 创建配置对象
retry_config = RetryConfig()