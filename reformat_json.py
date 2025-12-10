#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JSON 파일을 각 주차 객체가 한 줄인 형식으로 변환"""

import json

def reformat_json_file(input_file, output_file):
    """JSON 파일을 각 주차 객체가 한 줄인 형식으로 변환"""
    print(f"📖 파일 로드 중: {input_file}")
    
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    print(f"✅ 파일 로드 완료")
    print(f"💾 변환 중: {output_file}")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        # 연도별로 정렬
        sorted_years = sorted([k for k in data.keys() if k != 'metadata'])
        
        f.write('{\n')
        year_lines = []
        for year in sorted_years:
            year_data = data[year]
            desc = json.dumps(year_data['description'], ensure_ascii=False)
            week_lines = []
            for week in year_data['weeks']:
                week_str = json.dumps(week, ensure_ascii=False, separators=(',', ':'))
                week_lines.append(f'      {week_str}')
            weeks_str = '[\n' + ',\n'.join(week_lines) + '\n    ]'
            year_str = f'  "{year}": {{\n    "description": {desc},\n    "weeks": {weeks_str}\n  }}'
            year_lines.append(year_str)
        
        # metadata 추가
        metadata = data['metadata']
        metadata_items = []
        for key, value in metadata.items():
            if isinstance(value, str):
                metadata_items.append(f'    "{key}": {json.dumps(value, ensure_ascii=False)}')
            else:
                metadata_items.append(f'    "{key}": {value}')
        metadata_str = '{\n' + ',\n'.join(metadata_items) + '\n  }'
        year_lines.append(f'  "metadata": {metadata_str}')
        
        f.write(',\n'.join(year_lines))
        f.write('\n}')
    
    print(f"✅ 변환 완료!")

if __name__ == "__main__":
    input_file = 'data/weekly_rsi_reference copy.json'
    output_file = 'data/weekly_rsi_reference copy.json'
    reformat_json_file(input_file, output_file)

