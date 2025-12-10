#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RSI가 0인 항목 확인 스크립트"""

import json

# 파일 로드
with open('data/weekly_rsi_reference copy.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# RSI가 0인 항목 찾기
zero_items = []
for year in data.keys():
    if year == 'metadata':
        continue
    
    for week in data[year].get('weeks', []):
        if week.get('rsi', 0) == 0.0:
            zero_items.append({
                'year': year,
                'week': week['week'],
                'start': week['start'],
                'end': week['end'],
                'rsi': week['rsi']
            })

# 결과 출력
print(f"RSI가 0인 항목: 총 {len(zero_items)}개")
print("=" * 80)

# 연도별로 그룹화
by_year = {}
for item in zero_items:
    year = item['year']
    if year not in by_year:
        by_year[year] = []
    by_year[year].append(item)

# 연도별 출력
for year in sorted(by_year.keys()):
    items = by_year[year]
    print(f"\n{year}년: {len(items)}개 항목")
    for item in items[:10]:  # 각 연도당 최대 10개만 표시
        print(f"  - {item['week']}주차 ({item['start']}~{item['end']}): RSI = {item['rsi']}")
    if len(items) > 10:
        print(f"  ... 외 {len(items) - 10}개 더 있음")

print("\n" + "=" * 80)
print(f"총 {len(zero_items)}개의 RSI=0 항목이 발견되었습니다.")
