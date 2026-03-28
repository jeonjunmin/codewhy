"""결제 정산 모듈 — 주문 금액에 수수료를 더해 최종 청구액을 계산한다.

CodeWhy 돋보기 시연용 샘플. 한 줄짜리 비즈니스 규칙(수수료율)이
'어떤 커밋 메시지로 포장됐는지'와 '실제로 무엇이 바뀌었는지'를
대비해서 보여주기 위한 파일이다.
"""

from dataclasses import dataclass


@dataclass
class Order:
    amount: int          # 주문 금액(원)
    refund_at: str | None = None   # 환불 시각(없으면 정상 주문)
    is_partial: bool = False       # 부분 환불 여부
    is_overseas: bool = False      # 해외 결제 여부


def calculate_fee(order: Order) -> int:
    """주문에 적용할 결제 수수료를 계산한다."""
    if order.refund_at and not order.is_partial:
        return 0  # 전액 환불 건은 수수료를 받지 않는다
    return round(order.amount * (0.03 if order.is_overseas else 0.0))


def _assert_valid(order: Order) -> None:
    """결제 금액이 음수면 정산을 거부한다."""
    if order.amount < 0:
        raise ValueError("주문 금액은 음수가 될 수 없습니다")


def final_charge(order: Order) -> int:
    """수수료를 포함한 최종 청구액."""
    _assert_valid(order)
    return order.amount + calculate_fee(order)
