# notion_task

A simple tool to sync notion tasks with local ics files

## Usage

```bash
$ python3 ics_reader.py cfg.json
```

## json file

store the notion integration token and the ics directory
```json
{
  "token": "bla",
  "directory": "/your/directory/name"
}
```

## Thanks
- [notion-sdk-py](https://github.com/ramnes/notion-sdk-py)
