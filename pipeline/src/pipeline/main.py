#!/usr/bin/env python
from random import randint

from pydantic import BaseModel

from crewai.flow import Flow, listen, start

from pipeline.crews.poem_crew.poem_crew import AiRefine

import sys


class PoemState(BaseModel):
    repo: str
    pr_number : int


class PoemFlow(Flow[PoemState]):
    def __init__(self, repo: str, pr_number: int):
        self._repo = repo
        self._pr_number = pr_number
        super().__init__()
    
    def _create_initial_state(self):
        return PoemState(repo=self._repo, pr_number=self._pr_number)
    
    @start()
    def fetch_files(self):
        repo_name = self.state.repo
        pr_number = self.state.pr_number
        
        inputs = {
            'repo_name': repo_name,
            'pr_number': pr_number,
        }
        result=AiRefine().crew().kickoff(inputs=inputs)
        print(result)


def kickoff(repo: str, pr_number: int):
    poem_flow = PoemFlow(repo=repo,pr_number=pr_number)
    poem_flow.kickoff()


def plot():
    poem_flow = PoemFlow()
    poem_flow.plot()


if __name__ == "__main__":
   # Check if correct number of arguments provided
    repo_name = sys.argv[1]
    pr_number = int(sys.argv[2])
    
    
    
    kickoff(repo_name, pr_number)

