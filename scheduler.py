import os
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ_TAIPEI = ZoneInfo("Asia/Taipei")
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from linebot.v3.messaging import ApiClient, Configuration, MessagingApi, PushMessageRequest, TextMessage

logger = logging.getLogger(__name__)

NOTIFY_DAYS = {30: "一個月", 7: "一週", 3: "三天", 1: "一天"}
TIME_TOLERANCE_SECONDS = 90


def check_and_notify():
    from database import SessionLocal, Todo

    db = SessionLocal()
    try:
        todos = db.query(Todo).all()
        today = date.today()
        messages = []

        for todo in todos:
            days_left = (todo.due_date - today).days
            if days_left in NOTIFY_DAYS:
                label = NOTIFY_DAYS[days_left]
                lines = [
                    f"⏰ 【{label}後到期】",
                    f"📌 {todo.title}",
                    f"📅 到期日：{todo.due_date}",
                ]
                if todo.description:
                    lines.append(f"💬 備註：{todo.description}")
                messages.append("\n".join(lines))

        if not messages:
            logger.info("每日檢查完成，今天沒有需要通知的事項")
            return

        user_id = os.environ.get("LINE_USER_ID", "")
        if not user_id:
            logger.error("LINE_USER_ID 未設定，無法推播通知")
            return

        configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
        with ApiClient(configuration) as api_client:
            messaging_api = MessagingApi(api_client)
            for msg in messages:
                messaging_api.push_message(
                    PushMessageRequest(to=user_id, messages=[TextMessage(text=msg)])
                )

        logger.info(f"已推播 {len(messages)} 則通知")
    except Exception as e:
        logger.error(f"排程通知發生錯誤：{e}")
    finally:
        db.close()


def check_recurring_reminders():
    from database import SessionLocal, RecurringReminder

    db = SessionLocal()
    try:
        today = date.today()
        weekday = today.weekday()   # 0=Mon … 6=Sun
        day_of_month = today.day

        reminders = db.query(RecurringReminder).all()
        messages = []

        for r in reminders:
            if r.repeat_type == "weekly" and r.day_of_week == weekday:
                messages.append(f"🔔 【定期提醒】\n📌 {r.content}")
            elif r.repeat_type == "monthly" and r.day_of_month == day_of_month:
                messages.append(f"🔔 【定期提醒】\n📌 {r.content}")

        if not messages:
            logger.info("今天沒有符合的定期提醒")
            return

        user_id = os.environ.get("LINE_USER_ID", "")
        if not user_id:
            logger.error("LINE_USER_ID 未設定，無法推播定期提醒")
            return

        configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
        with ApiClient(configuration) as api_client:
            messaging_api = MessagingApi(api_client)
            for msg in messages:
                messaging_api.push_message(
                    PushMessageRequest(to=user_id, messages=[TextMessage(text=msg)])
                )

        logger.info(f"已推播 {len(messages)} 則定期提醒")
    except Exception as e:
        logger.error(f"定期提醒排程發生錯誤：{e}")
    finally:
        db.close()


def check_and_send_notifications():
    """每分鐘執行：查詢 notify_time 落在當下時間窗口內、尚未發送的待辦通知，
    合併同一收件人的多筆後一次推播，避免同人同時收到多則。"""
    from database import SessionLocal, Todo

    now = datetime.now(TZ_TAIPEI).replace(tzinfo=None)
    window_start = now - timedelta(seconds=TIME_TOLERANCE_SECONDS)
    window_end = now + timedelta(seconds=TIME_TOLERANCE_SECONDS)

    db = SessionLocal()
    try:
        todos = db.query(Todo).filter(
            Todo.notify_enabled == True,
            Todo.notified_at == None,
            Todo.notify_time >= window_start,
            Todo.notify_time <= window_end,
        ).all()

        if not todos:
            return

        per_user_messages = defaultdict(list)
        for todo in todos:
            targets = todo.notify_targets if isinstance(todo.notify_targets, list) else \
                      (json.loads(todo.notify_targets) if todo.notify_targets else [])
            msg_lines = [f"【待辦提醒】{todo.title}", f"到期日：{todo.due_date.strftime('%m/%d')}"]
            if todo.description:
                msg_lines.append(f"備註：{todo.description}")
            line = "\n".join(msg_lines)
            for user_id in targets:
                per_user_messages[user_id].append(line)

        if not per_user_messages:
            return

        configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
        with ApiClient(configuration) as api_client:
            messaging_api = MessagingApi(api_client)
            for user_id, messages in per_user_messages.items():
                combined = "\n\n".join(messages)
                try:
                    messaging_api.push_message(
                        PushMessageRequest(to=user_id, messages=[TextMessage(text=combined)])
                    )
                except Exception as e:
                    logger.error(f"[通知失敗] user={user_id} error={e}")

        sent_at = datetime.now(TZ_TAIPEI).replace(tzinfo=None)
        for todo in todos:
            todo.notified_at = sent_at
        db.commit()

        logger.info(f"已推播通知給 {len(per_user_messages)} 位使用者，共 {len(todos)} 筆待辦")
    except Exception as e:
        logger.error(f"即時通知排程發生錯誤：{e}")
    finally:
        db.close()


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Taipei")
    scheduler.add_job(
        check_and_notify,
        CronTrigger(hour=8, minute=0, timezone="Asia/Taipei"),
        id="daily_check",
        replace_existing=True,
    )
    scheduler.add_job(
        check_recurring_reminders,
        CronTrigger(hour=8, minute=0, timezone="Asia/Taipei"),
        id="recurring_check",
        replace_existing=True,
    )
    scheduler.add_job(
        check_and_send_notifications,
        "interval",
        minutes=1,
        id="notify_check",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("排程已啟動：每天 08:00 執行到期通知 & 定期提醒，每分鐘執行即時通知檢查")
    return scheduler
