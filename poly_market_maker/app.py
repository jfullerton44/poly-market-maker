import logging
import sys
from prometheus_client import start_http_server
import time

from py_clob_client.client import ClobClient, ApiCreds, OrderArgs

from poly_market_maker import value_market
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


class App:
    """Market maker keeper on Polymarket CLOB"""

    def __init__(self, args: list):
        setup_logging()
        self.logger = logging.getLogger(__name__)

        args = get_args(args)
        self.sync_interval = args.sync_interval

        # self.min_tick = args.min_tick
        # self.min_size = args.min_size

        # server to expose the metrics.
        self.metrics_server_port = args.metrics_server_port
        start_http_server(self.metrics_server_port)

        self.web3 = setup_web3(args.rpc_url, args.private_key)
        self.address = self.web3.eth.account.from_key(args.private_key).address

        self.clob_api = ClobApi(
            host=args.clob_api_url,
            chain_id=self.web3.eth.chain_id,
            private_key=args.private_key,
        )
        self.address = "0x47A58585dd90D396238376bf57CC6a0eFdCCAa28"

        self.gas_station = GasStation(
            strat=GasStrategy(args.gas_strategy),
            w3=self.web3,
            url=args.gas_station_url,
            fixed=args.fixed_gas_price,
        )
        self.contracts = Contracts(self.web3, self.gas_station)
        conditionIds = []
        if args.condition_id == "":
            conditionIds = self.get_condition_ids(args)
        else:
            conditionIds= args.condition_id.split(",")
        self.markets = []
        self.price_feeds = []
        self.order_book_managers = []
        self.strategy_managers = []
        i = 0
        for conditionId in conditionIds:
            mark = self.clob_api.client.get_market(conditionId)
            self.markets.append(Market(
                conditionId,
                self.clob_api.get_collateral_address(),
            ))

            self.price_feeds.append(PriceFeedClob(self.markets[len(self.markets)-1], self.clob_api))
            
            # res = []
            # next = ""
            # while len(res) == 0:
            #     resp = self.clob_api.client.get_markets(next_cursor = next)
            #     resp["data"] = resp["data"]
            #     res = [x for x in resp["data"] if x['rewards']['min_size'] != 0]
            #     next = resp["next_cursor"]
            #     print("Done!")
            # print(res[0])

            order_book_manager = OrderBookManager(
                args.refresh_frequency, max_workers=1, index=i, reward_spread=mark["rewards"]["max_spread"]
            )
            order_book_manager.get_orders_with(self.get_orders)
            order_book_manager.get_balances_with(self.get_balances)
            order_book_manager.cancel_orders_with(
                lambda order: self.clob_api.cancel_order(order.id)
            )
            order_book_manager.place_orders_with(self.place_order)
            order_book_manager.cancel_all_orders_with(
                lambda _: self.clob_api.cancel_all_orders()
            )
            order_book_manager.start()
            self.order_book_managers.append(order_book_manager)

            self.strategy_managers.append(StrategyManager(
                args.strategy,
                args.strategy_config,
                self.price_feeds[i],
                self.order_book_managers[i],
            ))
            i+=1

    """
    main
    """

    def main(self):
        self.logger.debug(self.sync_interval)
        with Lifecycle() as lifecycle:
            lifecycle.on_startup(self.startup)
            lifecycle.every(self.sync_interval, self.synchronize)  # Sync every 5s
            lifecycle.on_shutdown(self.shutdown)

    """
    lifecycle
    """

    def startup(self):
        self.logger.info("Running startup callback...")
        self.approve()
        time.sleep(5)  # 5 second initial delay so that bg threads fetch the orderbook
        self.logger.info("Startup complete!")

    def synchronize(self):
        """
        Synchronize the orderbook by cancelling orders out of bands and placing new orders if necessary
        """
        self.logger.debug("Synchronizing orderbook...")
        for strategy_manager in self.strategy_managers:
            strategy_manager.synchronize()
        self.logger.debug("Synchronized orderbook!")

    def shutdown(self):
        """
        Shut down the keeper
        """
        self.logger.info("Keeper shutting down...")
        for order_book_manager in self.order_book_managers:
            order_book_manager.cancel_all_orders()
        self.logger.info("Keeper is shut down!")

    """
    handlers
    """

    def get_balances(self, i) -> dict:
        """
        Fetch the onchain balances of collateral and conditional tokens for the keeper
        """
        self.logger.debug(f"Getting balances for address: {self.address}")

        collateral_balance = self.contracts.token_balance_of(
            self.clob_api.get_collateral_address(), self.address
        )
        token_A_balance = self.contracts.token_balance_of(
            self.clob_api.get_conditional_address(),
            self.address,
            self.markets[i].token_id(Token.A),
        )
        token_B_balance = self.contracts.token_balance_of(
            self.clob_api.get_conditional_address(),
            self.address,
            self.markets[i].token_id(Token.B),
        )
        gas_balance = self.contracts.gas_balance(self.address)

        keeper_balance_amount.labels(
            accountaddress=self.address,
            assetaddress=self.clob_api.get_collateral_address(),
            tokenid="-1",
        ).set(collateral_balance)
        keeper_balance_amount.labels(
            accountaddress=self.address,
            assetaddress=self.clob_api.get_conditional_address(),
            tokenid=self.markets[i].token_id(Token.A),
        ).set(token_A_balance)
        keeper_balance_amount.labels(
            accountaddress=self.address,
            assetaddress=self.clob_api.get_conditional_address(),
            tokenid=self.markets[i].token_id(Token.B),
        ).set(token_B_balance)
        keeper_balance_amount.labels(
            accountaddress=self.address,
            assetaddress="0x0",
            tokenid="-1",
        ).set(gas_balance)

        return {
            Collateral: collateral_balance,
            Token.A: token_A_balance,
            Token.B: token_B_balance,
        }

    def get_orders(self, i) -> list[Order]:
        orders = self.clob_api.get_orders(self.markets[i].condition_id)
        return [
            Order(
                size=order_dict["size"],
                price=order_dict["price"],
                side=Side(order_dict["side"]),
                token=self.markets[i].token(order_dict["token_id"]),
                id=order_dict["id"],
            )
            for order_dict in orders
        ]

    def place_order(self, new_order: Order, i) -> Order:
        order_id = self.clob_api.place_order(
            price=new_order.price,
            size=new_order.size,
            side=new_order.side.value,
            token_id=self.markets[i].token_id(new_order.token),
        )
        return Order(
            price=new_order.price,
            size=new_order.size,
            side=new_order.side,
            id=order_id,
            token=new_order.token,
        )

    def approve(self):
        """
        Approve the keeper on the collateral and conditional tokens
        """
        collateral = self.clob_api.get_collateral_address()
        conditional = self.clob_api.get_conditional_address()
        exchange = self.clob_api.get_exchange()

        self.contracts.max_approve_erc20(collateral, self.address, exchange)
        self.contracts.max_approve_erc1155(conditional, self.address, exchange)

    def get_condition_ids(self, args) -> list[str]:
        res = []
        next = ""
        while len(res) < 150 and next != "LTE=":
            resp = self.clob_api.client.get_markets(next_cursor = next)
            resp["data"] = resp["data"]
            res1 = [x for x in resp["data"] if x['rewards']['min_size'] != 0 and x['enable_order_book']== True and x["neg_risk"] == False]
            print(res1)
            if len(res1) > 0:
                for val in res1:
                    res.append(val)
            next = resp["next_cursor"]
            print("Done!")
        markets = []
        for item in res:
            print(item['question'], item['condition_id'])
            try:
                question = item["question"]
                conditionId = item["condition_id"]
                dailyRate = item["rewards"]["rates"][0]["rewards_daily_rate"]
                maxSpread = item["rewards"]["max_spread"]
                tokenId = item["tokens"][0]["token_id"]
                price = item["tokens"][0]["price"]
                markets.append(value_market.Value_Market(question,conditionId, dailyRate, maxSpread,tokenId,price, self.clob_api.client))
            except Exception as e:
                print(item["question"], item["condition_id"], "failure during parsing")
                print(e)
        markets.sort(key=lambda x:x.rewardPerDollar, reverse=True)
        conditionIds = []
        while len(conditionIds) < args.num_markets:
            print("Starting market with ",markets[len(conditionIds)].conditionId, markets[len(conditionIds)].question)
            conditionIds.append(markets[len(conditionIds)].conditionId)
        if args.print_conditions == "true" or args.print_conditions == "True":
            for market in markets:
                print(market.question,"Reward:", market.rewardPerDollar)
                print("       ",market.conditionId)
            sys.exit()
        return conditionIds
