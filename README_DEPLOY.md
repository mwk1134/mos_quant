# 🌐 SOXL 퀀트투자 시스템 외부 접속 가이드

## 🚀 Streamlit Cloud 배포 (추천)

### 1. GitHub 저장소 생성
1. GitHub.com에 로그인
2. "New repository" 클릭
3. 저장소 이름: `soxl-quant-webapp`
4. "Public" 선택 (무료 배포를 위해)
5. "Create repository" 클릭

### 2. 파일 업로드
```bash
# Git 초기화
git init
git add .
git commit -m "Initial commit"

# GitHub에 연결
git remote add origin https://github.com/[사용자명]/soxl-quant-webapp.git
git branch -M main
git push -u origin main
```

### 3. Streamlit Cloud 배포
1. https://share.streamlit.io 접속
2. "New app" 클릭
3. GitHub 계정 연결
4. 저장소 선택: `soxl-quant-webapp`
5. Main file path: `app.py`
6. "Deploy!" 클릭

### 4. 완료!
- 배포 완료 후 고유 URL 제공
- 예: `https://soxl-quant-webapp.streamlit.app`
- 이 URL을 어디서든 접속 가능!

## 🔧 다른 배포 방법들

### ngrok (임시 해결책)
```bash
# 1. ngrok 설치
# https://ngrok.com/download

# 2. 계정 생성 및 토큰 설정
ngrok config add-authtoken [토큰]

# 3. 터널 생성
ngrok http 8501
```

### Heroku 배포
```bash
# 1. Heroku CLI 설치
# 2. Procfile 생성
echo "web: streamlit run app.py --server.port=$PORT --server.address=0.0.0.0" > Procfile

# 3. requirements.txt 확인
# 4. Heroku에 배포
heroku create soxl-quant-app
git push heroku main
```

### VPS 배포 (AWS EC2)
```bash
# 1. EC2 인스턴스 생성 (Ubuntu)
# 2. SSH 접속
ssh -i key.pem ubuntu@[IP주소]

# 3. 환경 설정
sudo apt update
sudo apt install python3-pip
pip3 install -r requirements.txt

# 4. 서비스 실행
streamlit run app.py --server.address=0.0.0.0 --server.port=8501

# 5. 보안 그룹에서 8501 포트 열기
```

## 💰 비용 비교

| 방법 | 초기 비용 | 월 비용 | 설정 난이도 | 추천도 |
|------|-----------|---------|-------------|--------|
| Streamlit Cloud | 무료 | 무료 | ⭐⭐ | ⭐⭐⭐⭐⭐ |
| ngrok 무료 | 무료 | 무료 | ⭐ | ⭐⭐⭐ |
| Heroku | 무료 | $7+ | ⭐⭐⭐ | ⭐⭐⭐⭐ |
| VPS | 무료 | $5-10 | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| 라즈베리파이 | $50 | $1-2 | ⭐⭐⭐⭐ | ⭐⭐ |

## 🎯 추천 순서

1. **Streamlit Cloud** (무료, 쉬움)
2. **ngrok** (임시 사용)
3. **Heroku** (안정적)
4. **VPS** (완전 제어)

## 📱 모바일 접속 테스트

배포 완료 후:
1. 핸드폰에서 제공된 URL 접속
2. Wi-Fi와 데이터 모두에서 테스트
3. 모든 기능 정상 작동 확인

## 🔒 보안 고려사항

- 투자 관련 앱이므로 HTTPS 사용 필수
- 민감한 데이터는 환경변수로 관리
- 정기적인 백업 수행
- 접속 로그 모니터링

---

**🎉 배포 완료 후 어디서든 핸드폰으로 SOXL 퀀트투자 시스템을 사용할 수 있습니다!**
