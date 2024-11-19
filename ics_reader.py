#!/usr/bin/env python3
import os
import json
import time
import asyncio
import logging
import argparse
from datetime import datetime, timedelta
from icalendar import Calendar, Event, vUri
from notion_client import AsyncClient


def get_recently_modified_files(directory_path, minutes):
    """
    Find files modified within the specified number of minutes in the given directory.

    Args:
        directory_path (str): Path to the directory to search
        minutes (int): Number of minutes to look back

    Returns:
        list: List of tuples containing (filename, modification_time)
    """
    # Get current time
    current_time = time.time()
    time_threshold = current_time - (minutes * 60)

    recent_files = []

    # Walk through directory
    for root, dirs, files in os.walk(directory_path):
        for file in files:
            file_path = os.path.join(root, file)
            try:
                # Get file's last modification time
                mtime = os.path.getmtime(file_path)

                # Check if file was modified within the last 15 minutes
                if mtime > time_threshold:
                    # Convert timestamp to readable datetime
                    mod_time = datetime.fromtimestamp(mtime)
                    recent_files.append((file_path, mod_time))
            except OSError as e:
                print(f"Error accessing {file_path}: {e}")

    return sorted(recent_files, key=lambda x: x[1], reverse=True)


class NotionSync:
    def __init__(self, token_v2: str):
        self.notion = AsyncClient(auth=token_v2)
        self.task_db = "14294bcc8f9f80f6a581d0e605335999"
        self.task_template = {
            "parent": {"database_id": self.task_db},
            "properties": {
                "Name": {
                    "title": [{"type": "text", "text": {"content": None}}],
                },
                "Deadline": {
                    "type": "date",
                    "date": {"start": None},
                },
                "Status": {
                    "type": "status",
                    "status": {"id": "1"},
                },
            },
        }

    async def create_page(self, due_date: datetime, title: str):
        new_task = self.task_template.copy()
        new_task["properties"]["Name"]["title"][0]["text"]["content"] = title
        new_task["properties"]["Deadline"]["date"]["start"] = due_date.isoformat()
        response = await self.notion.pages.create(**new_task)
        return response["url"]

    async def patch_page(self, page_id: str, due_date: datetime, title: str):
        response = await self.notion.pages.retrieve(page_id)
        touched = False
        response_due_date = datetime.fromisoformat(
            response["properties"]["Deadline"]["date"]["start"]
        )
        response_title = response["properties"]["Name"]["title"][0]["text"]["content"]

        if touched or response_due_date != due_date:
            logging.info(f"Response start: {response_due_date}, new start: {due_date}")
            touched = True
        if touched or response_title != title:
            logging.info(
                f"Response title: {response['properties']['Name']['title'][0]['text']['content']}, new title: {title}"
            )
            touched = True
        if touched:
            data = {
                "page_id": page_id,
                "properties": {
                    "Name": {
                        "title": [{"type": "text", "text": {"content": title}}],
                    },
                    "Deadline": {
                        "type": "date",
                        "date": {"start": due_date.isoformat()},
                    },
                },
            }
            response = await self.notion.pages.update(**data)

    async def read_ics_file(self, file_path: str):
        logging.info(f"Reading file: {file_path}")
        with open(file_path, "rb") as file:
            cal = Calendar.from_ical(file.read())

        modified = False
        for component in cal.walk("VEVENT"):
            due_date = component.get("DTEND").dt
            title = component.get("SUMMARY")
            if component.get("ATTACH"):
                url = component.get("ATTACH")
                page_id = url.split("-")[-1]
                await self.patch_page(page_id, due_date, title)
            else:
                # new event need to sync to notion
                url = await self.create_page(due_date, title)
                attachment = vUri(url)
                attachment.params["FILENAME"] = "-".join(
                    url.split("/")[-1].split("-")[:-1]
                )
                component.add("ATTACH", attachment)
                modified = True

        if modified:
            with open(file_path, "wb") as output:
                output.write(cal.to_ical())


async def main(directory: str, token: str):
    recent_files = get_recently_modified_files(directory, 15)

    notion = NotionSync(token_v2=token)
    async with asyncio.TaskGroup() as tg:
        for file, _ in recent_files:
            tg.create_task(notion.read_ics_file(file))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    parser = argparse.ArgumentParser(description="Sync ics files to notion task")
    parser.add_argument(
        "json", nargs="?", type=str, default="cfg.json", help="json file path"
    )
    args = parser.parse_args()
    with open(args.json) as f:
        cfg = json.load(f)

    asyncio.run(main(cfg["directory"], cfg["token"]))
