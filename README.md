<div align="center">

  # Welcome to Project W!

  <img src="frontend/static/favicon.png" width="200"/>

  [![License: AGPLv3](https://img.shields.io/badge/License-agplv3-yellow.svg)](https://opensource.org/license/agpl-v3)
  [![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/ssciwr/project-W/ci.yml?branch=main)](https://github.com/ssciwr/project-W/actions/workflows/ci.yml)
  [![Documentation Status](https://readthedocs.org/projects/project-w/badge/?version=latest)](https://project-w.readthedocs.io/en/latest/?badge=latest)
  [![GitHub release](https://img.shields.io/github/v/release/ssciwr/project-w)](https://github.com/ssciwr/project-w/releases)
</div>

## What is this?

Project W is a self-hostable platform on which users can create transcripts of their audio files (speech-to-text). It leverages OpenAIs Whisper model for the actual transcription while providing an easy-to-use interface on which users can create and manage their transcription jobs.

## Why do we need this? Why not just use OpenAIs own service?

In short: OpenAIs service is not good enough when it comes to data privacy.

In some research fields at our university a lot of interviews (and transcriptions of these interviews) need to be done. Traditionally the transcriptions are done manually, however with recent advancements AI can do this job as good or even better than the average human and fully automated. Since the faculties are required to keep these interviews private they can't just give it to third-parties like OpenAI. Everything must stay inside the university. Furthermore Whisper has high hardware requirements (for the large models you need powerful GPUs) making it quite difficult or impossible for the average person to use on their work laptop/desktop. Also the setup of CUDA and whisper and its usage (it only has a CLI interface) is not something that the average user would like to do.

This is where Project W comes in: It is designed so that everything can be hosted by the university itself on powerful hardware (like an A100 GPU) while it is very easy to be used by the average person. Just go to the website, sign up and upload some files!

## Why are there three main components?

Project W consists of three components: The frontend/client, the backend, and the runner. They are all available in [this single mono-repository](https://github.com/ssciwr/project-W).

![UML-diagram](https://github.com/ssciwr/project-W/assets/70963316/717c278c-e985-47d4-9b97-3b861dbe99ca)

The backend and runner are written in Python, and we use FastAPI for the backends HTTP-Server. The Frontend is written in Svelte and SvelteKit so that it can be compiled into native Javascript, HTML and CSS. No Nodejs or anything other than a webserver (e.g. nginx) is required to serve the frontend. Of course you can also choose to write your own client with anything you like that can communicate with a REST API. This means you can use Project W with some bash or python script to automate certain tasks.

## Documentation

You can access the full documentation for administrators and developers [here](https://project-w.readthedocs.io). Most notably this includes installation and configuration instructions for all three components if you want to host them yourself.

![output](https://github.com/ssciwr/project-W/assets/70963316/2134852b-369c-4bda-a0f4-7575753414d9)

## E-Science-Tage 2025: Poster

We took part in the poster session of the E-Science-Tage 2025 conference. If you want to take a look at our abstract and poster you can [find it here](https://doi.org/10.11588/heidok.00036417).

## Acknowledgments

Parts of this repository were set up using the [SSC Cookiecutter for Python Packages](https://github.com/ssciwr/cookiecutter-python-package).
