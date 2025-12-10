#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JSON 파일을 한 줄 형식으로 변환 (각 주차 데이터를 한 줄에)"""

import json

def compact_json_dump(obj, fp, **kwargs):
    """각 주차 데이터를 한 줄로 저장하는 커스텀 JSON 덤프"""
    def format_week(week_obj):
        """주차 객체를 한 줄로 포맷"""
        return f'{{"start":"{week_obj["start"]}","end":"{week_obj["end"]}","week":{week_obj["week"]},"rsi":{week_obj["rsi"]}}}'
    
    def format_year(year_data):
        """연도 데이터를 포맷"""
        weeks_str = ','.join([format_week(w) for w in year_data['weeks']])
        return f'{{"description":"{year_data["description"]}","weeks":[{weeks_str}]}}'
    
    # 메타데이터 처리
    metadata = obj.get('metadata', {})
    metadata_str = ','.join([f'"{k}":"{v}"' if isinstance(v, str) else f'"{k}":{v}' 
                             for k, v in metadata.items()])
    
    # 연도별 데이터 처리
    year_items = []
    for year in sorted([k for k in obj.keys() if k != 'metadata']):
        year_str = format_year(obj[year])
        year_items.append(f'"{year}":{year_str}')
    
    # 전체 JSON 조합
    result = '{' + ','.join(year_items)
    if metadata:
        result += f',"metadata":{{{metadata_str}}}}'
    else:
        result += '}'
    
    fp.write(result)

# 파일 로드
input_file = 'data/weekly_rsi_reference copy.json'
output_file = 'data/weekly_rsi_reference copy.json'

print(f"📖 파일 로드 중: {input_file}")

with open(input_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

print(f"✅ 파일 로드 완료")

# 한 줄 형식으로 저장
print(f"💾 한 줄 형식으로 저장 중: {output_file}")

with open(output_file, 'w', encoding='utf-8') as f:
    compact_json_dump(data, f)

print(f"✅ 변환 완료!")
