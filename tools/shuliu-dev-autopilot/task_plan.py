from __future__ import annotations
from dataclasses import dataclass
import hashlib
import re
from pathlib import Path


TASK_ORDER=(28,29,30,31,32,33,34,35,36,27)
HEADER=re.compile(r"^### Task (\d+) — (.+?)\s*$",re.MULTILINE)


class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class PlannedTask:
    number:int
    title:str
    dependencies:tuple[int,...]
    release_gates:tuple[str,...]
    body:str


@dataclass(frozen=True)
class TaskCatalog:
    tasks:dict[int,PlannedTask]
    task_order:tuple[int,...]
    source_sha256:str
    source_path:Path

    def next_task(self,statuses):
        for number in self.task_order:
            if statuses.get(number)!="PASSED":
                return number
        return None

    def startable(self,number,statuses,baseline_completed=()):
        if number not in self.tasks or number not in self.task_order:
            return False
        previous=self.task_order[:self.task_order.index(number)]
        if any(statuses.get(n)!="PASSED" for n in previous):
            return False
        completed=set(baseline_completed)
        return all(d in completed or statuses.get(d)=="PASSED" for d in self.tasks[number].dependencies)


def _dependencies(body):
    match=re.search(r"前置依赖：([^\n]+)",body)
    if not match:
        raise PlanError("task has no explicit dependency declaration")
    clause=re.split(r"[；。]",match.group(1),maxsplit=1)[0]
    ranges=[]
    def take_range(m):
        a,b=int(m.group(1)),int(m.group(2))
        if b<a:raise PlanError("invalid dependency range")
        ranges.extend(range(a,b+1))
        return " "
    clause=re.sub(r"\b(?:Tasks?\s*)?(\d{1,2})\s*[–-]\s*(\d{1,2})",take_range,clause,flags=re.IGNORECASE)
    values={int(x) for x in re.findall(r"\b\d{1,2}\b",clause)}
    values.update(ranges)
    return tuple(sorted(values))


def load_catalog(path:Path):
    path=Path(path)
    try:text=path.read_text(encoding="utf-8")
    except OSError as exc:raise PlanError("implementation plan cannot be read") from exc
    headings=list(HEADER.finditer(text));tasks={}
    for index,header in enumerate(headings):
        number=int(header.group(1))
        if number not in TASK_ORDER:continue
        end=headings[index+1].start() if index+1<len(headings) else len(text)
        body=text[header.end():end]
        gates_match=re.search(r"Release Gate：([^\n]+)",body)
        if number==27 and not gates_match:
            # Task 27 makes the release manifest the single required gate list.
            gates=("all_required_gates_from_release_manifest",)
        else:
            if not gates_match:raise PlanError(f"Task {number} has no Release Gate")
            gates=tuple(re.findall(r"\x60([^\x60]+)\x60",gates_match.group(1)))
            if not gates:raise PlanError(f"Task {number} Release Gate is empty")
        tasks[number]=PlannedTask(number,header.group(2),_dependencies(body),gates,body)
    missing=set(TASK_ORDER)-set(tasks)
    if missing:raise PlanError("Implementation Plan is missing tasks: "+", ".join(map(str,sorted(missing))))
    return TaskCatalog(tasks,TASK_ORDER,hashlib.sha256(text.encode("utf-8")).hexdigest(),path.resolve())
