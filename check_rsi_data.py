import json

with open('data/weekly_rsi_reference.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

dates = sorted([k for k in data.keys()])
print('첫 날짜:', dates[0] if dates else None)
print('마지막 날짜:', dates[-1] if dates else None)
print('총', len(dates), '개 날짜')
print()
print('최근 20개 날짜:')
for d in dates[-20:]:
    rsi = data[d].get('rsi', 'N/A')
    print(f'  {d}: RSI={rsi}')

