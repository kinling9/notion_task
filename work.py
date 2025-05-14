#!/usr/bin/env python3
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

# Load environment variables
load_dotenv()

# Notion API configuration
NOTION_TOKEN = os.getenv("NOTION_TASK_API_KEY")
# Load configuration from JSON file
with open("config.json", "r") as f:
    config = json.load(f)
NOTION_DATABASE_ID = config.get("NOTION_DATABASE_ID")
notion = Client(auth=NOTION_TOKEN)

# Email configuration
EMAIL_SENDER = config.get("EMAIL_SENDER")
EMAIL_PASSWORD = config.get("EMAIL_PASSWORD")
EMAIL_RECIPIENT = config.get("EMAIL_RECIPIENT")
SMTP_SERVER = config.get("SMTP_SERVER", "smtp.qiye.aliyun.com")
SMTP_PORT = int(config.get("SMTP_PORT", "465"))

# Work hours configuration (24-hour format)
WORK_START_TIME = datetime.strptime(
    os.getenv("WORK_START_TIME", "09:00"), "%H:%M"
).time()
WORK_END_TIME = datetime.strptime(os.getenv("WORK_END_TIME", "18:30"), "%H:%M").time()
WORK_DAYS = [
    int(day) for day in os.getenv("WORK_DAYS", "0,1,2,3,4").split(",")
]  # 0 is Monday, 6 is Sunday

# Task statuses
STATUS_TODO = "Not started"
STATUS_IN_PROGRESS = "In progress"
STATUS_BLOCKED = "Blocked"
STATUS_COMPLETED = "Done"


class TaskManager:
    def __init__(self):
        """Initialize task manager"""
        self.tasks = []
        self.blocking_events = []

    def fetch_tasks_from_notion(self):
        """Fetch all tasks from Notion database"""
        try:
            response = notion.databases.query(
                database_id=NOTION_DATABASE_ID,
                filter={
                    "or": [
                        {"property": "Status", "status": {"equals": STATUS_TODO}},
                        {
                            "property": "Status",
                            "status": {"equals": STATUS_IN_PROGRESS},
                        },
                        {"property": "Status", "status": {"equals": STATUS_BLOCKED}},
                    ]
                },
            )

            self.tasks = []
            for page in response.get("results", []):
                # print(page)
                task = self._parse_notion_page(page)
                if task:
                    self.tasks.append(task)

            # Sort by priority and deadline
            self.tasks.sort(
                key=lambda x: (
                    -x.get("priority", 0),  # Higher priority first
                    x.get("deadline", datetime.max),  # Sooner deadlines first
                )
            )

            return self.tasks
        except Exception as e:
            print(f"ERROR in getting task from notion database: {e}")
            return []

    def fetch_blocking_events(self):
        """Get blocking events"""
        try:
            response = notion.databases.query(
                database_id=NOTION_DATABASE_ID,
                filter={
                    "and": [
                        {
                            "property": "Task type",
                            "multi_select": {"contains": "Blocker"},
                        },
                        {"property": "Status", "status": {"equals": STATUS_COMPLETED}},
                    ]
                },
            )

            self.blocking_events = []
            for page in response.get("results", []):
                event = self._parse_notion_page(page)
                if event:
                    self.blocking_events.append(event)

            # Sort by start time
            self.blocking_events.sort(key=lambda x: x.get("start_time", datetime.max))

            return self.blocking_events
        except Exception as e:
            print(f"Error fetching blocking events: {e}")
            return []

    def _parse_notion_page(self, page):
        """Parse Notion page to get task information"""
        try:
            properties = page.get("properties", {})

            # Get basic info
            task_id = page.get("id")
            task_name = (
                properties.get("Task name", {})
                .get("title", [{}])[0]
                .get("plain_text", "Untitled Task")
            )

            # Get status
            status_obj = properties.get("Status", {}).get("select", {})
            status = status_obj.get("name") if status_obj else STATUS_TODO

            # Get priority
            priority_obj = properties.get("Priority", {}).get("select", {})
            priority_map = {"High": 3, "Medium": 2, "Low": 1}
            priority = priority_map.get(
                priority_obj.get("name") if priority_obj else "Low", 1
            )

            # Get deadline
            deadline = None
            deadline_obj = properties.get("Due data", {}).get("date", {})
            if deadline_obj and deadline_obj.get("start"):
                deadline = datetime.fromisoformat(
                    deadline_obj.get("start").replace("Z", "+00:00")
                )

            # Get estimated duration (hours)
            duration_obj = properties.get("Effort level", {}).get("select", {})
            duration_map = {"Large": 2, "Medium": 1, "Small": 0.5}
            duration = duration_map.get(
                duration_obj.get("name") if duration_obj else "Low", 1
            )

            # Get task type
            task_type_obj = properties.get("Task type", {}).get("multi_select", [])
            task_types = (
                [item.get("name") for item in task_type_obj]
                if task_type_obj
                else ["work"]
            )
            # Check if task is blocked
            is_blocked = status == STATUS_BLOCKED

            # Get planned start and end times
            start_time = None
            end_time = None
            date_obj = properties.get("Plan time", {}).get("date", {})
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
                    # If there's a start time but no end time, calculate end time based on estimated duration
                    end_time = start_time + timedelta(hours=duration)

            return {
                "id": task_id,
                "name": task_name,
                "status": status,
                "priority": priority,
                "deadline": deadline,
                "duration": duration,
                "type": task_types,
                "is_blocked": is_blocked,
                "start_time": start_time,
                "end_time": end_time,
            }
        except Exception as e:
            print(f"Error parsing task: {e}")
            return None

    def is_work_time(self, dt=None):
        """Check if given time is work time"""
        if dt is None:
            dt = datetime.now()

        # Check if it's a workday (weekday(): 0 is Monday, 6 is Sunday)
        if dt.weekday() not in WORK_DAYS:
            return False

        # Check if within work hours
        current_time = dt.time()
        return WORK_START_TIME <= current_time <= WORK_END_TIME

    def is_blocked_time(self, dt):
        """Check if given time is occupied by a blocking event"""
        for event in self.blocking_events:
            start = event.get("start_time")
            end = event.get("end_time")
            if start and end and start <= dt <= end:
                return True
        return False

    def find_next_available_slot(self, task, start_from=None):
        """Find the next available time slot for a task"""
        if not start_from:
            start_from = datetime.now()

        # Work tasks can only be scheduled during work hours
        task_is_work = task.get("type") == "工作"

        # Estimated task duration
        duration = task.get("duration", 1.0)  # hours
        duration_delta = timedelta(hours=duration)

        # Start searching from current time
        current_time = start_from

        # Try to find a slot within the next 7 days
        end_search_time = start_from + timedelta(days=7)

        while current_time < end_search_time:
            # Check if it's work time (for work tasks)
            if task_is_work and not self.is_work_time(current_time):
                # Non-work time, jump to start of next workday
                current_time = self._next_work_day_start(current_time)
                continue

            # Check if blocked
            if self.is_blocked_time(current_time):
                # Find the end time of the blocking event
                next_time = self._next_available_after_blocking(current_time)
                current_time = next_time
                continue

            # Check end time
            end_time = current_time + duration_delta

            # For work tasks, ensure the entire task fits within work hours
            if task_is_work:
                work_end = datetime.combine(current_time.date(), WORK_END_TIME)

                # If the task would exceed the work end time
                if end_time > work_end:
                    # Move to start of next workday
                    current_time = self._next_work_day_start(current_time)
                    continue

            # Check if there are any blocking events before the end time
            blocked = False
            check_time = current_time
            while check_time <= end_time:
                if self.is_blocked_time(check_time):
                    blocked = True
                    break
                check_time += timedelta(minutes=15)  # Check every 15 minutes

            if blocked:
                current_time = self._next_available_after_blocking(check_time)
                continue

            # Found an available slot
            return current_time, end_time

        # If no suitable slot found, return None
        return None, None

    def _next_work_day_start(self, dt):
        """Get the start time of the next workday"""
        next_day = dt.date() + timedelta(days=1)
        days_checked = 0

        while days_checked < 7:  # Check up to 7 days ahead
            next_day_dt = datetime.combine(next_day, WORK_START_TIME)
            if next_day_dt.weekday() in WORK_DAYS:
                return next_day_dt
            next_day += timedelta(days=1)
            days_checked += 1

        # If no workday found in 7 days, return original time plus 7 days
        return dt + timedelta(days=7)

    def _next_available_after_blocking(self, dt):
        """Get the next available time after a blocking event ends"""
        for event in self.blocking_events:
            start = event.get("start_time")
            end = event.get("end_time")
            if start and end and start <= dt <= end:
                return end

        # If no specific blocking event found, move forward 30 minutes
        return dt + timedelta(minutes=30)

    def schedule_tasks(self):
        """Schedule time for all tasks"""
        # First get all unfinished tasks and blocking events
        self.fetch_tasks_from_notion()
        self.fetch_blocking_events()

        # Keep track of already scheduled time slots to avoid overlaps
        scheduled_times = []

        # Schedule each task
        for task in self.tasks:
            # Skip if task already has start and end times
            if task.get("start_time") and task.get("end_time"):
                scheduled_times.append((task.get("start_time"), task.get("end_time")))
                continue

            # Find available time slot
            start_time, end_time = self.find_next_available_slot(task)

            if start_time and end_time:
                # Update task schedule
                self.update_task_schedule(task.get("id"), start_time, end_time)
                scheduled_times.append((start_time, end_time))

    def update_task_schedule(self, task_id, start_time, end_time):
        """Update the scheduled time for a task in Notion"""
        try:
            # Convert to ISO format strings
            start_iso = start_time.isoformat()
            end_iso = end_time.isoformat()

            # Update Notion page
            notion.pages.update(
                page_id=task_id,
                properties={"计划时间": {"date": {"start": start_iso, "end": end_iso}}},
            )
            print(f"Updated schedule for task {task_id}: {start_time} - {end_time}")
            return True
        except Exception as e:
            print(f"Error updating task schedule: {e}")
            return False

    def update_task_status(self, task_id, status):
        """Update task status"""
        try:
            notion.pages.update(
                page_id=task_id, properties={"状态": {"select": {"name": status}}}
            )
            print(f"Updated task {task_id} status to: {status}")
            return True
        except Exception as e:
            print(f"Error updating task status: {e}")
            return False

    def handle_blocking_events(self):
        """Handle blocking events and reschedule affected tasks"""
        # Get all blocking events
        self.fetch_blocking_events()

        if not self.blocking_events:
            return

        # Mark affected tasks as blocked
        for task in self.tasks:
            start = task.get("start_time")
            end = task.get("end_time")

            if not start or not end:
                continue

            # Check overlap with any blocking event
            for event in self.blocking_events:
                event_start = event.get("start_time")
                event_end = event.get("end_time")

                if not event_start or not event_end:
                    continue

                # Check overlap
                if start <= event_end and end >= event_start:
                    # Task overlaps with blocking event, mark as blocked
                    self.update_task_status(task.get("id"), STATUS_BLOCKED)

        # Reschedule all blocked tasks
        self.schedule_tasks()

    def generate_daily_plan(self):
        """Generate today's work plan"""
        # Get latest tasks
        self.fetch_tasks_from_notion()

        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)

        # Filter today's tasks
        today_tasks = []
        for task in self.tasks:
            start = task.get("start_time")
            if start and start.date() == today:
                today_tasks.append(task)

        # Sort by start time
        today_tasks.sort(key=lambda x: x.get("start_time"))

        # Generate email content
        subject = f"Today's Work Plan ({today.strftime('%Y-%m-%d')})"

        if not today_tasks:
            body = "No tasks scheduled for today."
        else:
            body = f"<h2>Today's Work Plan ({today.strftime('%Y-%m-%d')})</h2>\n\n"
            body += "<table border='1' cellpadding='5' style='border-collapse: collapse;'>\n"
            body += (
                "<tr><th>Time</th><th>Task</th><th>Priority</th><th>Status</th></tr>\n"
            )

            for task in today_tasks:
                start = (
                    task.get("start_time").strftime("%H:%M")
                    if task.get("start_time")
                    else "Not Scheduled"
                )
                end = (
                    task.get("end_time").strftime("%H:%M")
                    if task.get("end_time")
                    else "Not Scheduled"
                )
                time_str = f"{start} - {end}"

                priority_map = {3: "High", 2: "Medium", 1: "Low"}
                priority = priority_map.get(task.get("priority"), "Low")

                body += f"<tr><td>{time_str}</td><td>{task.get('name')}</td><td>{priority}</td><td>{task.get('status')}</td></tr>\n"

            body += "</table>\n\n"

            # Add blocking event reminders
            blocking_events_today = [
                e
                for e in self.blocking_events
                if e.get("start_time") and e.get("start_time").date() == today
            ]
            if blocking_events_today:
                body += "<h3>Today's Blocking Events:</h3>\n<ul>\n"
                for event in blocking_events_today:
                    start = (
                        event.get("start_time").strftime("%H:%M")
                        if event.get("start_time")
                        else "Not Scheduled"
                    )
                    end = (
                        event.get("end_time").strftime("%H:%M")
                        if event.get("end_time")
                        else "Not Scheduled"
                    )
                    body += f"<li>{event.get('name')}: {start} - {end}</li>\n"
                body += "</ul>\n"

        return subject, body

    def send_email(self, subject, body):
        """Send email"""
        try:
            # Create email message
            message = MIMEMultipart("alternative")
            message["From"] = EMAIL_SENDER
            message["To"] = EMAIL_RECIPIENT
            message["Subject"] = subject

            html_part = MIMEText(body, "html")
            message.attach(html_part)

            # Connect to SMTP server and send
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
                # server.starttls()
                server.login(EMAIL_SENDER, EMAIL_PASSWORD)
                server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, message.as_string())

            print(f"Email sent: {subject}")
            return True
        except Exception as e:
            print(f"Error sending email: {e}")
            return False

    def daily_email_reminder(self):
        """Send daily task email reminder"""
        subject, body = self.generate_daily_plan()
        self.send_email(subject, body)

    def monitor_notion_changes(self):
        """Monitor changes in Notion database"""
        # Get current state
        current_tasks = {
            task.get("id"): task for task in self.fetch_tasks_from_notion()
        }
        current_blocking = {
            event.get("id"): event for event in self.fetch_blocking_events()
        }

        # Periodically check for changes
        while True:
            time.sleep(60)  # Check every minute

            # Get latest state
            new_tasks = {
                task.get("id"): task for task in self.fetch_tasks_from_notion()
            }
            new_blocking = {
                event.get("id"): event for event in self.fetch_blocking_events()
            }

            # Check for new tasks
            for task_id, task in new_tasks.items():
                if task_id not in current_tasks:
                    print(f"Detected new task: {task.get('name')}")
                    # Schedule the new task
                    start_time, end_time = self.find_next_available_slot(task)
                    if start_time and end_time:
                        self.update_task_schedule(task_id, start_time, end_time)

            # Check for new blocking events
            if len(new_blocking) != len(current_blocking):
                print(
                    "Detected changes in blocking events, reprocessing affected tasks"
                )
                self.handle_blocking_events()

            # Update current state
            current_tasks = new_tasks
            current_blocking = new_blocking


def setup_notion_database():
    """Set up Notion database structure"""
    try:
        # Check if database exists
        db = notion.databases.retrieve(database_id=NOTION_DATABASE_ID)
        print(
            f"Connected to Notion database: {db.get('title', [{}])[0].get('plain_text', 'Untitled')}"
        )
        return True
    except Exception as e:
        print(f"Failed to connect to Notion database: {e}")
        print(
            "Please make sure you have created a Notion database and set the correct environment variables"
        )
        print("The database should contain the following properties:")
        print("- Name (title): Task name")
        print("- Status (select): To Do, In Progress, Blocked, Completed")
        print("- Priority (select): High, Medium, Low")
        print("- Type (select): Work, Personal, Blocking Event")
        print("- Deadline (date): Task deadline")
        print("- Estimated Duration (number): Estimated number of hours for the task")
        print("- Scheduled Time (date): System-scheduled start and end times")
        return False


def main():
    """Main function"""
    print("Starting Notion automated task management system...")

    # Check Notion database setup
    if not setup_notion_database():
        return

    # Initialize task manager
    task_manager = TaskManager()

    # Daily morning email reminder
    schedule.every().day.at("09:00").do(task_manager.daily_email_reminder)

    # Hourly blocking event handling
    schedule.every(1).hours.do(task_manager.handle_blocking_events)

    # Daily afternoon task scheduling
    schedule.every().day.at("17:00").do(task_manager.schedule_tasks)

    print("Initialization complete, running initial task scheduling...")

    # Initial run to schedule tasks and send notifications
    task_manager.schedule_tasks()
    task_manager.daily_email_reminder()

    print("Task manager started, press Ctrl+C to exit")

    # Start background thread to monitor Notion changes
    import threading

    monitor_thread = threading.Thread(target=task_manager.monitor_notion_changes)
    monitor_thread.daemon = True
    monitor_thread.start()

    # Keep main program running and execute scheduled tasks
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("Program stopped")


if __name__ == "__main__":
    main()
