from scripts.brahma_1hao_analysis import run_analysis, run_dual_analysis
import sys
def main():
    import argparse
    parser = argparse.ArgumentParser(description='梵天分析入口')
    parser.add_argument('--symbol', default=None)
    parser.add_argument('--symbols', nargs='+', default=['BTCUSDT','ETHUSDT'])
    parser.add_argument('--direction', default='LONG', choices=['LONG','SHORT'])
    args = parser.parse_args()
    if args.symbol:
        print(run_analysis(args.symbol, args.direction))
    else:
        run_dual_analysis(args.symbols, args.direction)
if __name__=="__main__": main()
