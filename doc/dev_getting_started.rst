Development - Getting started
=============================

.. note::
   Before contributing code please also read :ref:`code_style-label` for the guidelines we use for code styling in this project and :ref:`test_setup-label` for how to write test cases for your code.

This is a guide for how to get a basic Project-W development environment up and running, and how to use it. Please also refer to :ref:`manual_installation-label` for additional instructions (however focused on deployment).

Basic setup & usage instructions
--------------------------------

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

      docker compose --profile dev up -d

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
Please note that while hot reloading should automatically be enabled, it currently does not monitor changes to the project_W_lib.
If you make changes here you have to stop and restart the development server.
Also, sometimes it can occur that the process doesn't exist properly when reloading/stopping.
In these cases, search for a process that includes `worker-1` in the name and kill it (e.g. using htop).

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

You are now ready to go! You should now be able to access the frontend under http://localhost:5173. Hot reloading is enabled as well.

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

You are now ready to go! Note that by default, Whisper caches downloaded models in ``$HOME/.cache/whisper/``. If you would like the runner to download the models into a different directory, set ``whisper_settings.model_cache_dir`` in your ``config.yml`` to the desired directory.
The runner has no kind of hot reloading, after making changes to it you have to restart the process.


.. _nix_develop-label:

Alternatively: Nix
``````````````````

If you have Nix installed you can set up development environments with just one command (you don't have to use NixOS for this, you just need Nix). This will also set up pre-commit for you. You can use the same process for all three components of the project:

Clone the repository and enter its directory. After that run

   .. code-block:: console

      nix develop .#<environment name>

The following environments are available: ``project_W-env`` (for the backend), ``project_W_runner-env`` (for the runner), ``doc-env`` (for generating the docs), ``tests-env`` (for writing the tests), and ``root`` (for the frontend and if you're in neither of the subdirectories). All of them also set up pre-commit.

We recommend to use `Direnv <https://github.com/nix-community/nix-direnv>`_ to automatically enter the correct environment when navigating between the directories. For this we already include the required ``.envrc`` files, you just need to run ``direnv allow`` once in every directory that has one of these files in it.


Locally building the containers and running the tests
-----------------------------------------------------

Our tests are system tests instead of component tests, meaning they simulate a production deployment as closely as possible while testing & fuzzing the backend's API.
We chose this approach since the Project-W components are highly interconnected between each other, as well as with other services like PostgreSQL, Redis, and SMTP.
To be able to test the full behavior of all components we have to start all these services as docker containers in a deployment-like setup, and then we use pytest in combination with some fixtures for controlling the backend and runner container to make HTTP requests to the backend's API.
For more information about the testing rationale see :doc:`testing_and_code-quality`.

If you want to run the test suite locally (instead of only in GitHub Actions), you have to first build the required docker containers including the changes you made.
For this we provide a script ``build-container.sh`` that uses dockers buildx for highest compatibility while being compatible with podman (by running the build inside a container) and building up a local cache in your `/tmp` for faster rebuilds.
For the CI tests, you need the backend as well as runner_dummy containers.
For this run:

   .. code-block:: console

      ./build-container.sh backend

and

   .. code-block:: console

      ./build-container.sh runner_dummy

Next, make sure that you have the required service containers up and running. The tests use the same docker-compose.yml as the development setup, but don't require the `dev` profile (although it doesn't hurt to start the `dev` containers as well):

   .. code-block:: console

      docker compose up -d

After waiting some time for the containers to start up, you should now be able to run the tests. For this do:

   .. code-block:: console

      cd tests
      uv sync
      source .venv/bin/activate
      pytest --timeout=45 project_W_tests/
