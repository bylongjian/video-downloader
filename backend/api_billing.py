"""支付回调保持原始 body 验签；订单读取只使用当前登录身份。"""

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import billing
from .auth import AuthenticatedUser, get_current_user

router = APIRouter(prefix='/api/billing', tags=['会员与支付'])


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_key: str = Field(min_length=1, max_length=200)
    plan_code: Literal['vip_30d']


@router.get('/plans')
def list_plans():
    return {'success': True, 'data': billing.list_plans()}


@router.post('/checkout', status_code=201)
def create_checkout(req: CheckoutRequest, user: AuthenticatedUser = Depends(get_current_user)):
    return {'success': True, 'data': billing.create_checkout(user.id, req.request_key, req.plan_code)}


@router.get('/orders')
def list_orders(limit: int = Query(default=20, ge=1, le=100),
                cursor: str | None = Query(default=None, max_length=1024),
                user: AuthenticatedUser = Depends(get_current_user)):
    return {'success': True, 'data': billing.list_orders(user.id, limit, cursor)}


@router.get('/orders/{id}')
def get_order(id: str, user: AuthenticatedUser = Depends(get_current_user)):
    return {'success': True, 'data': billing.get_order(user.id, id)}


@router.post('/webhook')
async def stripe_webhook(request: Request, stripe_signature: str = Header(default='', alias='Stripe-Signature')):
    payload = await request.body()
    try:
        await run_in_threadpool(billing.process_stripe_event, payload, stripe_signature)
    except sqlite3.OperationalError as exc:
        raise HTTPException(503, {'code': 'PAYMENT_SERVICE_UNAVAILABLE', 'error': '付款确认暂不可用，请稍后重试'}) from exc
    return {'success': True, 'data': {'received': True}}
