from py_clob_client.client import ClobClient, ApiCreds, OrderArgs

from poly_market_maker.args import get_args
from poly_market_maker.price_feed import PriceFeedClob
from poly_market_maker.gas import GasStation, GasStrategy
from poly_market_maker.utils import setup_logging, setup_web3
from poly_market_maker.order import Order, Side
from poly_market_maker.market import Market
from poly_market_maker.token import Token, Collateral
from poly_market_maker.clob_api import ClobApi
from poly_market_maker.lifecycle import Lifecycle
from poly_market_maker.orderbook import OrderBookManager
from poly_market_maker.contracts import Contracts
from poly_market_maker.metrics import keeper_balance_amount
from poly_market_maker.strategy import StrategyManager

class Value_Market:
    def __init__(self, question: str, conditionId: str, reward_daily_rate: int, reward_max_spread:int, token_id: str, token_price:float, clobClient: ClobClient):
        self.question = question
        self.conditionId = conditionId
        self.reward_daily_rate = reward_daily_rate
        self.reward_max_spread = reward_max_spread/100
        self.clobClient = clobClient
        self.token_id = token_id
        self.token_price = token_price
        orderbook = self.clobClient.get_order_book(self.token_id)
        minPrice = self.token_price - self.reward_max_spread
        maxPrice = self.token_price + self.reward_max_spread
        total = 0
        for bid in orderbook.bids:
            if float(bid.price) >= minPrice:
                total += float(bid.price) * float(bid.size)
        for ask in orderbook.asks:
            if float(ask.price) <= maxPrice:
                total += float(ask.price) * float(ask.size)
        if total == 0:
            self.rewardPerDollar = 0
        else:
            self.rewardPerDollar = self.reward_daily_rate / total
        maxBid = float(orderbook.bids.pop().price)
        minAsk = float(orderbook.asks.pop().price)
        if minAsk - maxBid > self.reward_max_spread * 1.5:
            self.rewardPerDollar = 0
        if self.token_price < 0.1 or self.token_price > 0.9:
            self.rewardPerDollar = 0
        if self.reward_daily_rate < 50:
            self.rewardPerDollar = self.rewardPerDollar / 10
        elif self.reward_daily_rate < 100:
            self.rewardPerDollar = self.rewardPerDollar / 2
        print(orderbook)


    