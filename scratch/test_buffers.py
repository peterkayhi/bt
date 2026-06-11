import os
import sys
import datetime
import logging
import backtrader as bt

# Add root folder to path so we can import from the workspace
sys.path.append("/Users/peterkay/Developer/bt")

from papa_bear import PapaBearStrategy
from main import CustomCSVData, ETFS, DATA_DIR

class BufferTestingStrategy(PapaBearStrategy):
    buffer_val = 0.0

    def __init__(self):
        super().__init__()
        # Override logger level or disable handlers to keep console clean
        self.logger.setLevel(logging.WARNING)

    def rebalance_portfolio(self):
        rankings = []
        for data in self.etfs:
            if len(data) > 0:
                momentum = self.calculate_momentum(data)
                rankings.append((data, momentum))
        rankings.sort(key=lambda x: x[1], reverse=True)
        top_3 = [r[0] for r in rankings[:3]]
        
        for data in self.etfs:
            position = self.getposition(data)
            if position.size > 0 and data not in top_3:
                self.order_target_percent(data, target=0.0)

        # Allocate equally with safety buffer
        target_weight = (1.0 - self.buffer_val) / 3.0
        for data in top_3:
            self.order_target_percent(data, target=target_weight)

def run_test(buffer_val):
    cerebro = bt.Cerebro()
    from_date = datetime.datetime(2014, 1, 1)
    to_date = datetime.datetime.now()
    
    for ticker in ETFS:
        filepath = os.path.join(DATA_DIR, f"{ticker}.csv")
        data = CustomCSVData(dataname=filepath, fromdate=from_date, todate=to_date)
        cerebro.adddata(data, name=ticker)

    cerebro.broker.setcash(100000.0)
    cerebro.broker.setcommission(commission=0.001)
    cerebro.broker.set_coc(True)
    cerebro.broker.set_checksubmit(False)

    # Track margin failures
    margin_failures = 0
    
    def log_observer(order):
        nonlocal margin_failures
        if order.status in [order.Margin]:
            margin_failures += 1

    # We can add a strategy and intercept order notifications
    class TrackedStrategy(BufferTestingStrategy):
        buffer_val = buffer_val
        def notify_order(self, order):
            log_observer(order)
            super().notify_order(order)

    cerebro.addstrategy(TrackedStrategy)
    cerebro.run()
    
    final_val = cerebro.broker.getvalue()
    return final_val, margin_failures

if __name__ == "__main__":
    buffers = [0.0, 0.005, 0.01, 0.015, 0.02, 0.025, 0.03]
    print(f"{'Buffer':<10}{'Final Value':<20}{'Margin Failures':<15}")
    print("-" * 45)
    for b in buffers:
        val, failures = run_test(b)
        print(f"{b:<10.3f}${val:<19,.2f}{failures:<15}")
