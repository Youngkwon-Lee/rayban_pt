# 서버 자동 시작 설정

## 설치

```bash
mkdir -p ~/.config/systemd/user
cp rayban-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable rayban-bridge.service
systemctl --user start rayban-bridge.service
loginctl enable-linger $USER
```

## 관리 명령어

```bash
# 상태 확인
systemctl --user status rayban-bridge.service

# 로그 보기
journalctl --user -u rayban-bridge.service -f

# 재시작
systemctl --user restart rayban-bridge.service

# 중지
systemctl --user stop rayban-bridge.service
```
