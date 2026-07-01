import os
import re
from datetime import date
from linebot.v3.messaging import MessagingApi, ReplyMessageRequest, TextMessage
from sqlalchemy.orm import Session
from database import Todo, RecurringReminder, Member

AUTHORIZED_USER_IDS = {
    uid.strip()
    for uid in os.environ.get("LINE_USER_ID", "").split(",")
    if uid.strip()
}

LIFF_URL = "https://liff.line.me/2010527914-eR2RcmE7"

_WEEKDAY_MAP = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_WEEKDAY_NAMES = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]


def _parse_recurring_add(text: str):
    m = re.match(r"^定期新增\s+每週([一二三四五六日天])\s+(.+)$", text)
    if m:
        return {
            "repeat_type": "weekly",
            "day_of_week": _WEEKDAY_MAP[m.group(1)],
            "day_of_month": None,
            "content": m.group(2),
        }
    m = re.match(r"^定期新增\s+每月(\d{1,2})號\s+(.+)$", text)
    if m:
        day = int(m.group(1))
        if 1 <= day <= 31:
            return {
                "repeat_type": "monthly",
                "day_of_week": None,
                "day_of_month": day,
                "content": m.group(2),
            }
    return None


def _parse_recurring_delete(text: str):
    m = re.match(r"^定期刪除\s+(\d+)$", text)
    return int(m.group(1)) if m else None


def _recurrence_label(r: RecurringReminder) -> str:
    if r.repeat_type == "weekly":
        return f"每{_WEEKDAY_NAMES[r.day_of_week]}"
    return f"每月{r.day_of_month}號"


def _parse_complete(text: str):
    match = re.match(r"^完成\s+(\d+)$", text.strip())
    return int(match.group(1)) if match else None


def _days_emoji(days_left: int) -> str:
    if days_left < 0:
        return "🔴"
    if days_left <= 3:
        return "🔴"
    if days_left <= 7:
        return "🟡"
    return "🟢"


def _reply(messaging_api: MessagingApi, reply_token: str, text: str):
    messaging_api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=text)],
        )
    )


def _upsert_member(user_id: str, db: Session, messaging_api: MessagingApi) -> None:
    try:
        profile = messaging_api.get_profile(user_id)
        display_name = profile.display_name
    except Exception:
        display_name = None

    member = db.query(Member).filter(Member.line_user_id == user_id).first()
    if member is None:
        db.add(Member(line_user_id=user_id, display_name=display_name))
    else:
        member.display_name = display_name
    db.commit()


def _get_display_name(user_id: str, db: Session) -> str:
    member = db.query(Member).filter(Member.line_user_id == user_id).first()
    if member and member.display_name:
        return member.display_name
    return user_id[-6:]  # fallback: 末6碼


def handle_message(sender_id: str, text: str, reply_token: str, db: Session, messaging_api: MessagingApi):
    _upsert_member(sender_id, db, messaging_api)

    if sender_id not in AUTHORIZED_USER_IDS:
        _reply(messaging_api, reply_token, "此功能僅限授權使用者使用。")
        return

    text = text.strip()

    # 新增 → 導向 LIFF 表單
    if text in ("新增", "新增待辦", "add"):
        reply = (
            f"📝 請點選連結開啟新增表單：\n"
            f"{LIFF_URL}"
        )

    # 清單
    elif text in ("清單", "查看", "list"):
        todos = db.query(Todo).order_by(Todo.due_date).all()
        if not todos:
            reply = "目前沒有待辦事項 🎉"
        else:
            lines = ["📋 團隊待辦清單：\n"]
            for t in todos:
                days_left = (t.due_date - date.today()).days
                emoji = _days_emoji(days_left)
                creator = _get_display_name(t.created_by, db) if t.created_by else "?"
                lines.append(
                    f"{emoji} [{t.id}] {t.title}\n"
                    f"    📅 {t.due_date}（剩 {days_left} 天）by {creator}"
                )
            reply = "\n".join(lines)

    # 完成
    elif text.startswith("完成"):
        todo_id = _parse_complete(text)
        if todo_id:
            todo = db.query(Todo).filter(Todo.id == todo_id).first()
            if todo:
                title = todo.title
                db.delete(todo)
                db.commit()
                reply = f"✅ 已完成並刪除：{title}"
            else:
                reply = f"找不到編號 {todo_id} 的待辦事項"
        else:
            reply = "格式錯誤！請使用：完成 編號\n例如：完成 3"

    # 定期新增
    elif text.startswith("定期新增"):
        parsed = _parse_recurring_add(text)
        if parsed:
            reminder = RecurringReminder(**parsed)
            db.add(reminder)
            db.commit()
            db.refresh(reminder)
            reply = (
                f"🔔 已新增定期提醒\n"
                f"📌 {reminder.content}\n"
                f"🗓 重複時間：{_recurrence_label(reminder)}"
            )
        else:
            reply = (
                "格式錯誤！請使用：\n"
                "定期新增 每週X 提醒內容\n"
                "定期新增 每月X號 提醒內容\n"
                "例如：定期新增 每週一 繳瓦斯費\n"
                "例如：定期新增 每月1號 繳房租"
            )

    # 定期清單
    elif text in ("定期清單", "定期提醒"):
        reminders = db.query(RecurringReminder).order_by(RecurringReminder.id).all()
        if not reminders:
            reply = "目前沒有定期提醒 🎉"
        else:
            lines = ["🔔 定期提醒清單：\n"]
            for r in reminders:
                lines.append(f"[{r.id}] {r.content}\n    🗓 {_recurrence_label(r)}")
            reply = "\n".join(lines)

    # 定期刪除
    elif text.startswith("定期刪除"):
        rid = _parse_recurring_delete(text)
        if rid:
            reminder = db.query(RecurringReminder).filter(RecurringReminder.id == rid).first()
            if reminder:
                content = reminder.content
                db.delete(reminder)
                db.commit()
                reply = f"🗑 已刪除定期提醒：{content}"
            else:
                reply = f"找不到編號 {rid} 的定期提醒"
        else:
            reply = "格式錯誤！請使用：定期刪除 編號\n例如：定期刪除 2"

    else:
        reply = (
            "可用指令：\n"
            "📝 新增 → 開啟表單新增待辦\n"
            "📋 清單 → 查看所有待辦\n"
            "✅ 完成 編號 → 標記完成\n"
            "─────────────\n"
            "🔔 定期新增 每週X 提醒內容\n"
            "🔔 定期新增 每月X號 提醒內容\n"
            "📋 定期清單\n"
            "🗑 定期刪除 編號"
        )

    _reply(messaging_api, reply_token, reply)