import os
import datetime
import time
import schedule
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from notion_client import Client
from dotenv import load_dotenv
from datetime import datetime, timedelta

# 加载环境变量
load_dotenv()

# Notion API配置
NOTION_TOKEN = os.getenv("NOTION_TASK_API_KEY")
# Load configuration from JSON file
with open("config.json", "r") as f:
    config = json.load(f)
NOTION_DATABASE_ID = config.get("NOTION_DATABASE_ID")
notion = Client(auth=NOTION_TOKEN)

# 邮件配置
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

# 工作时间配置 (24小时制)
WORK_START_TIME = datetime.strptime(
    os.getenv("WORK_START_TIME", "09:00"), "%H:%M"
).time()
WORK_END_TIME = datetime.strptime(os.getenv("WORK_END_TIME", "18:00"), "%H:%M").time()
WORK_DAYS = [
    int(day) for day in os.getenv("WORK_DAYS", "0,1,2,3,4").split(",")
]  # 0是周一，6是周日

# 任务状态
STATUS_TODO = "待办"
STATUS_IN_PROGRESS = "进行中"
STATUS_BLOCKED = "阻塞"
STATUS_COMPLETED = "已完成"


class TaskManager:
    def __init__(self):
        """初始化任务管理器"""
        self.tasks = []
        self.blocking_events = []

    def fetch_tasks_from_notion(self):
        """从Notion数据库获取所有任务"""
        try:
            response = notion.databases.query(
                database_id=NOTION_DATABASE_ID,
                filter={
                    "or": [
                        {"property": "状态", "select": {"equals": STATUS_TODO}},
                        {"property": "状态", "select": {"equals": STATUS_IN_PROGRESS}},
                        {"property": "状态", "select": {"equals": STATUS_BLOCKED}},
                    ]
                },
            )

            self.tasks = []
            for page in response.get("results", []):
                task = self._parse_notion_page(page)
                if task:
                    self.tasks.append(task)

            # 按优先级和截止日期排序
            self.tasks.sort(
                key=lambda x: (
                    -x.get("priority", 0),  # 高优先级在前
                    x.get("deadline", datetime.max),  # 近期截止日期在前
                )
            )

            return self.tasks
        except Exception as e:
            print(f"从Notion获取任务时出错: {e}")
            return []

    def fetch_blocking_events(self):
        """获取阻塞事件"""
        try:
            response = notion.databases.query(
                database_id=NOTION_DATABASE_ID,
                filter={
                    "and": [
                        {"property": "类型", "select": {"equals": "阻塞事件"}},
                        {
                            "property": "状态",
                            "select": {"not_equals": STATUS_COMPLETED},
                        },
                    ]
                },
            )

            self.blocking_events = []
            for page in response.get("results", []):
                event = self._parse_notion_page(page)
                if event:
                    self.blocking_events.append(event)

            # 按开始时间排序
            self.blocking_events.sort(key=lambda x: x.get("start_time", datetime.max))

            return self.blocking_events
        except Exception as e:
            print(f"获取阻塞事件时出错: {e}")
            return []

    def _parse_notion_page(self, page):
        """解析Notion页面获取任务信息"""
        try:
            properties = page.get("properties", {})

            # 获取基本信息
            task_id = page.get("id")
            task_name = (
                properties.get("名称", {})
                .get("title", [{}])[0]
                .get("plain_text", "无标题任务")
            )

            # 获取状态
            status_obj = properties.get("状态", {}).get("select", {})
            status = status_obj.get("name") if status_obj else STATUS_TODO

            # 获取优先级
            priority_obj = properties.get("优先级", {}).get("select", {})
            priority_map = {"高": 3, "中": 2, "低": 1}
            priority = priority_map.get(
                priority_obj.get("name") if priority_obj else "低", 1
            )

            # 获取截止日期
            deadline = None
            deadline_obj = properties.get("截止日期", {}).get("date", {})
            if deadline_obj and deadline_obj.get("start"):
                deadline = datetime.fromisoformat(
                    deadline_obj.get("start").replace("Z", "+00:00")
                )

            # 获取预计时长（小时）
            duration = 1.0  # 默认1小时
            duration_obj = properties.get("预计时长", {}).get("number")
            if duration_obj is not None:
                duration = float(duration_obj)

            # 获取任务类型
            task_type_obj = properties.get("类型", {}).get("select", {})
            task_type = task_type_obj.get("name") if task_type_obj else "工作"

            # 获取是否被阻塞
            is_blocked = status == STATUS_BLOCKED

            # 获取计划开始和结束时间
            start_time = None
            end_time = None
            date_obj = properties.get("计划时间", {}).get("date", {})
            if date_obj:
                if date_obj.get("start"):
                    start_time = datetime.fromisoformat(
                        date_obj.get("start").replace("Z", "+00:00")
                    )
                if date_obj.get("end"):
                    end_time = datetime.fromisoformat(
                        date_obj.get("end").replace("Z", "+00:00")
                    )
                elif start_time:
                    # 如果有开始时间但没有结束时间，根据预计时长计算结束时间
                    end_time = start_time + timedelta(hours=duration)

            return {
                "id": task_id,
                "name": task_name,
                "status": status,
                "priority": priority,
                "deadline": deadline,
                "duration": duration,
                "type": task_type,
                "is_blocked": is_blocked,
                "start_time": start_time,
                "end_time": end_time,
            }
        except Exception as e:
            print(f"解析任务时出错: {e}")
            return None

    def is_work_time(self, dt=None):
        """判断给定时间是否为工作时间"""
        if dt is None:
            dt = datetime.now()

        # 检查是否是工作日 (weekday(): 0是周一，6是周日)
        if dt.weekday() not in WORK_DAYS:
            return False

        # 检查是否在工作时间内
        current_time = dt.time()
        return WORK_START_TIME <= current_time <= WORK_END_TIME

    def is_blocked_time(self, dt):
        """判断给定时间是否被阻塞事件占用"""
        for event in self.blocking_events:
            start = event.get("start_time")
            end = event.get("end_time")
            if start and end and start <= dt <= end:
                return True
        return False

    def find_next_available_slot(self, task, start_from=None):
        """为任务寻找下一个可用的时间段"""
        if not start_from:
            start_from = datetime.now()

        # 工作任务只能安排在工作时间
        task_is_work = task.get("type") == "工作"

        # 预计任务时长
        duration = task.get("duration", 1.0)  # 小时
        duration_delta = timedelta(hours=duration)

        # 从当前时间开始寻找可用时间段
        current_time = start_from

        # 最多尝试寻找未来7天内的时间段
        end_search_time = start_from + timedelta(days=7)

        while current_time < end_search_time:
            # 检查是否是工作时间（如果是工作任务）
            if task_is_work and not self.is_work_time(current_time):
                # 非工作时间，跳到下一个工作日的开始
                current_time = self._next_work_day_start(current_time)
                continue

            # 检查是否被阻塞
            if self.is_blocked_time(current_time):
                # 找到阻塞这个时间的事件的结束时间
                next_time = self._next_available_after_blocking(current_time)
                current_time = next_time
                continue

            # 检查结束时间
            end_time = current_time + duration_delta

            # 如果是工作任务，确保整个任务都在工作时间内
            if task_is_work:
                work_end = datetime.combine(current_time.date(), WORK_END_TIME)

                # 如果任务会超出当天的工作结束时间
                if end_time > work_end:
                    # 移到下一个工作日开始
                    current_time = self._next_work_day_start(current_time)
                    continue

            # 检查任务结束前是否有阻塞事件
            blocked = False
            check_time = current_time
            while check_time <= end_time:
                if self.is_blocked_time(check_time):
                    blocked = True
                    break
                check_time += timedelta(minutes=15)  # 以15分钟为间隔检查

            if blocked:
                current_time = self._next_available_after_blocking(check_time)
                continue

            # 找到可用时间段
            return current_time, end_time

        # 如果找不到合适的时间段，返回None
        return None, None

    def _next_work_day_start(self, dt):
        """获取下一个工作日的开始时间"""
        next_day = dt.date() + timedelta(days=1)
        days_checked = 0

        while days_checked < 7:  # 最多检查未来7天
            next_day_dt = datetime.combine(next_day, WORK_START_TIME)
            if next_day_dt.weekday() in WORK_DAYS:
                return next_day_dt
            next_day += timedelta(days=1)
            days_checked += 1

        # 如果未来7天都没有工作日，返回原时间加7天
        return dt + timedelta(days=7)

    def _next_available_after_blocking(self, dt):
        """获取阻塞事件结束后的下一个可用时间"""
        for event in self.blocking_events:
            start = event.get("start_time")
            end = event.get("end_time")
            if start and end and start <= dt <= end:
                return end

        # 如果没有找到具体的阻塞事件，向前推进30分钟
        return dt + timedelta(minutes=30)

    def schedule_tasks(self):
        """为所有任务安排时间"""
        # 先获取所有未完成的任务和阻塞事件
        self.fetch_tasks_from_notion()
        self.fetch_blocking_events()

        # 记录已经安排的时间段，避免任务重叠
        scheduled_times = []

        # 对每个任务进行安排
        for task in self.tasks:
            # 如果任务已经有明确的开始和结束时间，就不重新安排
            if task.get("start_time") and task.get("end_time"):
                scheduled_times.append((task.get("start_time"), task.get("end_time")))
                continue

            # 查找可用时间段
            start_time, end_time = self.find_next_available_slot(task)

            if start_time and end_time:
                # 更新任务时间
                self.update_task_schedule(task.get("id"), start_time, end_time)
                scheduled_times.append((start_time, end_time))

    def update_task_schedule(self, task_id, start_time, end_time):
        """更新Notion中任务的计划时间"""
        try:
            # 转换为ISO格式字符串
            start_iso = start_time.isoformat()
            end_iso = end_time.isoformat()

            # 更新Notion页面
            notion.pages.update(
                page_id=task_id,
                properties={"计划时间": {"date": {"start": start_iso, "end": end_iso}}},
            )
            print(f"已更新任务 {task_id} 的计划时间: {start_time} - {end_time}")
            return True
        except Exception as e:
            print(f"更新任务计划时间时出错: {e}")
            return False

    def update_task_status(self, task_id, status):
        """更新任务状态"""
        try:
            notion.pages.update(
                page_id=task_id, properties={"状态": {"select": {"name": status}}}
            )
            print(f"已更新任务 {task_id} 的状态为: {status}")
            return True
        except Exception as e:
            print(f"更新任务状态时出错: {e}")
            return False

    def handle_blocking_events(self):
        """处理阻塞事件，重新安排被阻塞的任务"""
        # 获取所有阻塞事件
        self.fetch_blocking_events()

        if not self.blocking_events:
            return

        # 标记受影响的任务为阻塞状态
        for task in self.tasks:
            start = task.get("start_time")
            end = task.get("end_time")

            if not start or not end:
                continue

            # 检查任务是否与任何阻塞事件重叠
            for event in self.blocking_events:
                event_start = event.get("start_time")
                event_end = event.get("end_time")

                if not event_start or not event_end:
                    continue

                # 检查重叠
                if start <= event_end and end >= event_start:
                    # 任务与阻塞事件重叠，标记为阻塞状态
                    self.update_task_status(task.get("id"), STATUS_BLOCKED)

        # 重新安排所有阻塞状态的任务
        self.schedule_tasks()

    def generate_daily_plan(self):
        """生成今日工作计划"""
        # 获取最新任务
        self.fetch_tasks_from_notion()

        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)

        # 筛选今天的任务
        today_tasks = []
        for task in self.tasks:
            start = task.get("start_time")
            if start and start.date() == today:
                today_tasks.append(task)

        # 按开始时间排序
        today_tasks.sort(key=lambda x: x.get("start_time"))

        # 生成邮件内容
        subject = f"今日工作计划 ({today.strftime('%Y-%m-%d')})"

        if not today_tasks:
            body = "今天没有安排任务。"
        else:
            body = f"<h2>今日工作计划 ({today.strftime('%Y-%m-%d')})</h2>\n\n"
            body += "<table border='1' cellpadding='5' style='border-collapse: collapse;'>\n"
            body += "<tr><th>时间</th><th>任务</th><th>优先级</th><th>状态</th></tr>\n"

            for task in today_tasks:
                start = (
                    task.get("start_time").strftime("%H:%M")
                    if task.get("start_time")
                    else "未安排"
                )
                end = (
                    task.get("end_time").strftime("%H:%M")
                    if task.get("end_time")
                    else "未安排"
                )
                time_str = f"{start} - {end}"

                priority_map = {3: "高", 2: "中", 1: "低"}
                priority = priority_map.get(task.get("priority"), "低")

                body += f"<tr><td>{time_str}</td><td>{task.get('name')}</td><td>{priority}</td><td>{task.get('status')}</td></tr>\n"

            body += "</table>\n\n"

            # 添加阻塞事件提醒
            blocking_events_today = [
                e
                for e in self.blocking_events
                if e.get("start_time") and e.get("start_time").date() == today
            ]
            if blocking_events_today:
                body += "<h3>今日阻塞事件:</h3>\n<ul>\n"
                for event in blocking_events_today:
                    start = (
                        event.get("start_time").strftime("%H:%M")
                        if event.get("start_time")
                        else "未安排"
                    )
                    end = (
                        event.get("end_time").strftime("%H:%M")
                        if event.get("end_time")
                        else "未安排"
                    )
                    body += f"<li>{event.get('name')}: {start} - {end}</li>\n"
                body += "</ul>\n"

        return subject, body

    def send_email(self, subject, body):
        """发送邮件"""
        try:
            # 创建邮件内容
            message = MIMEMultipart("alternative")
            message["From"] = EMAIL_SENDER
            message["To"] = EMAIL_RECIPIENT
            message["Subject"] = subject

            html_part = MIMEText(body, "html")
            message.attach(html_part)

            # 连接SMTP服务器并发送
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_SENDER, EMAIL_PASSWORD)
                server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, message.as_string())

            print(f"邮件已发送: {subject}")
            return True
        except Exception as e:
            print(f"发送邮件时出错: {e}")
            return False

    def daily_email_reminder(self):
        """发送每日任务邮件提醒"""
        subject, body = self.generate_daily_plan()
        self.send_email(subject, body)

    def monitor_notion_changes(self):
        """监控Notion数据库变化"""
        # 获取当前状态
        current_tasks = {
            task.get("id"): task for task in self.fetch_tasks_from_notion()
        }
        current_blocking = {
            event.get("id"): event for event in self.fetch_blocking_events()
        }

        # 定期检查变化
        while True:
            time.sleep(60)  # 每分钟检查一次

            # 获取最新状态
            new_tasks = {
                task.get("id"): task for task in self.fetch_tasks_from_notion()
            }
            new_blocking = {
                event.get("id"): event for event in self.fetch_blocking_events()
            }

            # 检查是否有新任务
            for task_id, task in new_tasks.items():
                if task_id not in current_tasks:
                    print(f"检测到新任务: {task.get('name')}")
                    # 安排新任务
                    start_time, end_time = self.find_next_available_slot(task)
                    if start_time and end_time:
                        self.update_task_schedule(task_id, start_time, end_time)

            # 检查是否有新阻塞事件
            if len(new_blocking) != len(current_blocking):
                print("检测到阻塞事件变化，重新处理受影响的任务")
                self.handle_blocking_events()

            # 更新当前状态
            current_tasks = new_tasks
            current_blocking = new_blocking


def setup_notion_database():
    """设置Notion数据库结构"""
    try:
        # 检查数据库是否存在
        db = notion.databases.retrieve(database_id=NOTION_DATABASE_ID)
        print(
            f"已连接到Notion数据库: {db.get('title', [{}])[0].get('plain_text', '未命名')}"
        )
        return True
    except Exception as e:
        print(f"连接Notion数据库失败: {e}")
        print("请确保您已创建Notion数据库并设置正确的环境变量")
        print("数据库应包含以下属性:")
        print("- 名称 (title): 任务名称")
        print("- 状态 (select): 待办、进行中、阻塞、已完成")
        print("- 优先级 (select): 高、中、低")
        print("- 类型 (select): 工作、个人、阻塞事件")
        print("- 截止日期 (date): 任务截止日期")
        print("- 预计时长 (number): 任务预计花费小时数")
        print("- 计划时间 (date): 系统安排的任务开始和结束时间")
        return False


def main():
    """主函数"""
    print("正在启动Notion自动化任务管理系统...")

    # 检查Notion数据库设置
    if not setup_notion_database():
        return

    # 初始化任务管理器
    task_manager = TaskManager()

    # 每天早上发送今日计划
    schedule.every().day.at("07:30").do(task_manager.daily_email_reminder)

    # 每小时处理阻塞事件
    schedule.every(1).hours.do(task_manager.handle_blocking_events)

    # 每天下午重新安排任务
    schedule.every().day.at("17:00").do(task_manager.schedule_tasks)

    print("初始化完成，进行首次任务安排...")

    # 首次运行时安排任务并发送通知
    task_manager.schedule_tasks()
    task_manager.daily_email_reminder()

    print("任务管理器已启动，按Ctrl+C退出")

    # 启动后台线程监控Notion变化
    import threading

    monitor_thread = threading.Thread(target=task_manager.monitor_notion_changes)
    monitor_thread.daemon = True
    monitor_thread.start()

    # 保持主程序运行并执行计划任务
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("程序已停止")


if __name__ == "__main__":
    main()
