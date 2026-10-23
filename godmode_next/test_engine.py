from engine import analyze, ema, rsi


def candles(n=60):
    out=[]
    price=1.1
    for i in range(n):
        price += 0.0001 if i%7 else -0.00003
        out.append({"time":1700000000+i*5,"open":price-0.00002,"high":price+0.00008,"low":price-0.00008,"close":price})
    return out


def test_ema_length_and_seed():
    values=ema([1,2,3,4],3)
    assert len(values)==4 and values[0]==1


def test_analyzer_waits_for_insufficient_candles():
    result=analyze(candles(10))
    assert result["direction"]=="WAIT"
    assert result["ready"] is False


def test_analyzer_outputs_transparent_components():
    result=analyze(candles())
    assert result["ready"] is True
    assert result["direction"] in ("BUY/CALL","SELL/PUT","WAIT")
    assert 0 <= result["score"] <= 100
    assert "di_spread" in result["components"]


def test_malformed_candles_fail_closed():
    result=analyze([{"close":"bad"}]*40)
    assert result["direction"]=="WAIT"
    assert result["score"]==0
