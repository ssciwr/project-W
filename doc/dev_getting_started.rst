Development - Getting started
=============================

.. note::
   Before contributing code please also read :ref:`code_style-label` for the guidelines we use for code styling in this project and :ref:`test_setup-label` for how to write test cases for your code.

This is a guide for how to get a basic Project-W development environment up and running. Please also refer to :ref:`manual_installation-label` for additional instructions (however focused on deployment).

Setup instructions
------------------

We switched to a monorepo that contains all Project-W components, including backend, frontend, and runner. Each component might require different setups (e.g. backend and runner might even require different python versions), so keep that in mind. We provide nix development shells that automatically provide you with all packages to need with the correct version, and also handle stuff like installing pre-commit hooks. It's not required to use nix though, you can also choose to install the required tools yourself:

- ``uv``: We use uv for python runtime, dependency and venv management for all Python projects. See `uv installation instructions <https://docs.astral.sh/uv/getting-started/installation/>`_

- ``ffmpeg``: Required for the runner if you don't want it to just execute in dummy mode

- ``nodejs`` and ``pnpm`` are required for frontend development (we currently use nodejs 24)

- ``podman`` (or docker, but we recommend podman for personal computers) might be helpful to set up dependencies for the Project-W backend

Regardless which component you want to develop on, start of by cloning the repository and entering it:

   .. code-block:: console

      git clone https://github.com/ssciwr/project-W.git && cd project-W

You can now start all the components required to run the backend using docker/podman. For this run:

   .. code-block:: console

      docker compose up

in the repository root. This will setup development PostgreSQL, Redis, Mailpit (SMTP), OpenLDAP, and Keycloak servers plus pgadmin and redisinsight for database debugging. It will configure everything automatically as well (i.e. setup a PostgreSQL database, connect pgadmin to postgres, setup OpenLDAP users and a Keycloak realm). Give it some time to start everything up, and then you can visit the tools in your browser:

- pgadmin: http://localhost:8080

- redisinsight: http://localhost:5540

- Mailpit: http://localhost:8025

- Keycloak: http://localhost:8081

You can now proceed with starting up the Project-W backend. The provided default config file for it is already configured to connect to all these docker components.

Backend
```````

1. Enter the ``backend`` directory

   .. code-block:: console

      cd backend

2. Sync all the dependencies:

   .. code-block:: console

      uv sync --dev

3. Enter the venv:

   .. code-block:: console

      source .venv/bin/activate

4. Startup the backend development server:

   .. code-block:: console

      ./run.sh

You are now ready to go! You should now be able to access the Swagger UI of your running backend under http://localhost:5000/docs.

Frontend
````````


1. Enter the ``frontend`` directory

   .. code-block:: console

      cd frontend

2. Install all project dependencies:

   .. code-block:: console

      pnpm install

3. Startup the frontend development server:

   .. code-block:: console

      pnpm dev

You are now ready to go! You should now be able to access the frontend under http://localhost:5173.

Runner
``````

1. First, you need to create a runner token for your local Project-W backend. Refer to :doc:`connect_runner_backend` for how to do that, while using ``http://localhost:5000`` as your backend url.

2. Enter the ``runner`` directory

   .. code-block:: console

      cd runner

3. Sync all the dependencies. If you don't want to download all the whisper-related dependencies and just want to use the runner in it's dummy mode (where it doesn't actually transcribe anything and always just returns the same dummy transcript), then you can also omit the ``--all-extras`` argument:

   .. code-block:: console

      uv sync --dev --all-extras

4. Enter the venv:

   .. code-block:: console

      source .venv/bin/activate

5. Replace the ``<your runner token>`` placeholder in the runner `config.yml` file with the runner token you obtained in step 1.

6. Startup the runner:

   .. code-block:: console

      ./run.sh

You are now ready to go! Note that by default, Whisper caches downloaded models in ``$HOME/.cache/whisper/``. If you would like
the runner to download the models into a different directory, set ``whisper_settings.model_cache_dir`` in your ``config.yml`` to the desired directory.

.. _nix_develop-label:

Alternatively: Nix
``````````````````

If you have Nix installed you can set up development environments with just one command (you don't have to use NixOS for this, you just need Nix). This will also set up pre-commit for you. You can use the same process for all three components of the project:

Clone the repository and enter its directory. After that run

   .. code-block:: console

      nix develop .#<environment name>

The following environments are available: ``project_W-env`` (for the backend), ``project_W_runner-env`` (for the runner), ``doc-env`` (for generating the docs), ``tests-env`` (for writing the tests), and ``root`` (for the frontend and if you're in neither of the subdirectories). All of them also set up pre-commit.

We recommend to use `Direnv <https://github.com/nix-community/nix-direnv>`_ to automatically enter the correct environment when navigating between the directories. For this we already include the required ``.envrc`` files, you just need to run ``direnv allow`` once in every directory that has one of these files in it.

Usage instructions
------------------

Backend
```````

First make sure that you have a PostgreSQL, Redis, SMTP, and optionally OIDC and LDAP instances running (e.g. using podman).

Then you need to edit the provided dummy ``config.yml`` file with your values. This file is for development purposes only and should not be used in production! Refer to :ref:`description_backend_config-label` for how to do that. If everything is ready, you can just start the backend with:

   .. code-block:: console

      ./run.sh

The backend will now run under the url `http://localhost:5000`, with the API docs available under `http://localhost:5000/docs`. The development webserver should also restart automatically when making changes to any code.

Frontend
````````

You can start a development server:

   .. code-block:: console

      pnpm dev

Now you can access the website over the url `http://localhost:5173` in your browser of choice and use the browsers development tools for debugging. The development server also supports hot module reloading which means that it will seamlessly update components on the website after you made changes to it without you even having to refresh the site in the browser.

The development build variables are declared in the file ``.env.development``. We currently just have one variable: ``PUBLIC_BACKEND_BASE_URL``. It defines the url of the backend that the frontend should use. If it is not defined then the frontend will assume that the backend is hosted on the same origin than the frontend. The default value is set to the port under which the development server of the backend runs per default (on the same machine). You can also set/overwrite this by setting an environment variable in your terminal.

If you want to compile the project into raw HTML, CSS and Javascript files  then run

   .. code-block:: console

      pnpm build

It will output those files into the ``build`` directory. If you plan on serving these on a different origin than the backend then you want to set ``PUBLIC_BACKEND_BASE_URL`` to the backends url before building. Either do this in the terminal as an environment variable or create a file ``.env.production`` to set it more permanently.

Runner
``````

First make sure to have a backend instance up and running and that you have obtained a runner token from that instance. Refer to :doc:`connect_runner_backend` for how to do that.

Then you need to edit the provided dummy ``config.yml`` file with your values. This file is for development purposes only and should not be used in production! Refer to :ref:`description_runner_config-label` for how to do that. If everything is ready, you can just start the runner with:

You can use the ``run.sh`` script to start the runner:

   .. code-block:: bash

      ./run.sh

Alternatively if you don't want to run the whisperx component of the runner you can also add the ``--dummy`` CLI option to the command inside ``run.sh``. This will result in the runner not doing any actual transcribing but can be a good option for testing purposes.
