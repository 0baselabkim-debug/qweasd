"""Explicit feature-control execution; never runs KIS or GPT."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.historical_v2.krx_recovery import main
if __name__=='__main__': main(sys.argv[1])
