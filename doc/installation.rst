Installation
============

The following installation guides are for Linux only. Theoretically all the components of this software should be possible to deploy onto other operating systems as well however this is not tested and not supported. You will have to be on your own for that.

.. note::
   The config files shown here are for a setup with local Project-W accounts only. If you want to setup LDAP and/or OIDC, please refer to :doc:`example_configs` for more advanced example configs.

.. important::
   If you plan to host the backend public to the internet: Make sure to only serve the backend over https regardless of your installation method! Without ssl encryption sensitive information like passwords, tokens for the clients and runners or user data will be transmitted in clear text!

.. note::
   Prerequisites: Make sure that your DNS and Firewall settings are correct. In particular, the domain that you want to use has to point to the ip address of your server, and your servers firewall needs to allow incoming tcp traffic on your http port

Docker
------

We provide two docker images, one for the backend that also serves the frontend at the same time, one that runs cleanup jobs and other periodic tasks with cron, and one for the runner. In the following we assume that you want to host our backend, periodic background tasks, and client/frontend on the same server, and the runner on a different one. If this assumption doesn't hold for you (e.g. if you want the frontend to be served by a different server than the backends API), then you may have to write your own Dockerfiles and docker-compose.yml or choose a different installation method like NixOS ;).

.. note::
   For both docker images there are multiple docker image tags. The examples below will use the 'latest' tag which will pull the latest stable release (recommended). If you want to pull the development version (latest git commit on main branch), then choose the 'main' tag instead. Alternatively you can also pinpoint the docker image to a specific version. Go to the Packages section of the GitHub repository to find out which tags are available. To use a tag other than 'latest' add it to the end of the 'image:' lines in the docker-compose.yml files below like this: image: <source>/<name>:<tag>

.. _docker_backend_frontend-label:

Backend, Frontend & background jobs
````````````````````````````````````

Choose between the following instructions depending on your setup. If you are unsure which setup to choose and you want the SSL certificate generation to be as easy as possible then :ref:`reverse_proxy-label` is probably for you.

Standalone
''''''''''

This will setup the backend/frontend without a reverse proxy or any additional components. This guide assumes that you know how to get an SSL certificate for the backend yourself (e.g. by using a self-signed certificate).

1. Install Docker: Refer to your distros package manager / the `Docker documentation <https://docs.docker.com/engine/install/>`_ for this
2. Create initial directory structure and enter project-W directory:

   .. code-block:: console

      mkdir -p project-W/project-W-data/certs && mkdir project-W/project-W-data/postgres && cd project-W

3. To run the backend you need a config.yml file that configures it. You can start off with the following example (don't forget to replace the <placeholders>!) and modify it to your needs if necessary. Put this config file into ./project-W-data. Refer to :doc:`example_configs` for more advanced example configs and to :ref:`description_backend_config-label` for more information about all the configuration options.

   .. warning::

      Please make sure to store the secrets that are read from an environment variable here in a secret way on your server! Some of these secrets would allow a bad actor to gain full access to Project-W including access to all user data!

      Please also make sure to not loose your secret_key as it is used for encrypting contents of the database. Refer to :doc:`database_encryption` for more information on that.

   In this setup, the secret_key and the smtp password are being read from the environment variables 'PROJECT_W_SECRET_KEY' and 'PROJECT_W_SMTP_PASSWORD'. If you want you can also choose to set them here directly in the config, but if you do so please take appropriate measures to keep this config file secret!

   .. code-block:: yaml

      client_url: https://<your domain>/#
      web_server:
      ssl:
        allowed_hosts:
          - localhost
          - <your domain>
        cert_file: '/etc/xdg/project-W/certs/cert.pem'
        key_file: '/etc/xdg/project-W/certs/key.pem'
      postgres_connection_string: !ENV 'postgresql://project_w:${POSTGRES_PASSWORD}@postgres:5432/project_w'
      redis_connection:
        connection_string: 'redis://redis:6379/project-W'
      security:
        secret_key: !ENV ${SECRET_KEY}
        local_account:
          user_provisioning:
            0:
              email: <email of your admin user>
              password: !ENV ${ADMIN_PASSWORD}
              is_admin: true
      smtp_server:
        hostname: <your smtp servers domain>
        port: <smtp port of smtp server>
        secure: <starttls or ssl>
        sender_email: <email address that should send emails to your users>
        username: <probably same as above>
        password: !ENV ${SMTP_PASSWORD}

4. Put your ssl certs into ./project-W-data/certs. Name the cert and key files as specified in the config above (cert.pem and key.pem respectively)
5. Put docker-compose.yml in the current directory. Use the following config and make same adjustments if needed (make sure to replace the <placeholders>!):

   .. code-block:: yaml

      services:
        postgres:
          image: postgres:17
          restart: unless-stopped
          environment:
            - POSTGRES_USER=project_w
            - POSTGRES_PASSWORD=${PROJECT_W_POSTGRES_PASSWORD}
          healthcheck:
            test: ["CMD-SHELL", "pg_isready -U project_w -d project_w"]
            interval: 10s
            retries: 3
            start_period: 30s
            timeout: 10s
          volumes:
            - ./project-W-data/postgres/:/var/lib/postgresql/data
        redis:
          image: redis:8
          restart: unless-stopped
          healthcheck:
            test: ["CMD", "redis-cli", "ping"]
            interval: 10s
            retries: 3
            start_period: 30s
            timeout: 10s
        project-w:
          image: ghcr.io/ssciwr/project-w
          restart: unless-stopped
          depends_on:
            postgres:
              condition: service_healthy
            redis:
              condition: service_healthy
          healthcheck:
            test: ["CMD", "curl", "-fk", "https://localhost:5000/api/about"]
            interval: 10s
            retries: 3
            start_period: 30s
            timeout: 10s
          volumes:
            - ./project-W-data/:/etc/xdg/project-W/
          environment:
            - SECRET_KEY=${PROJECT_W_SECRET_KEY}
            - SMTP_PASSWORD=${PROJECT_W_SMTP_PASSWORD}
            - POSTGRES_PASSWORD=${PROJECT_W_POSTGRES_PASSWORD}
            - ADMIN_PASSWORD=${PROJECT_W_ADMIN_PASSWORD}
          ports:
            - 443:5000
        project-w_cron:
          image: ghcr.io/ssciwr/project-w_cron
          restart: unless-stopped
          depends_on:
            postgres:
              condition: service_healthy
            redis:
              condition: service_healthy
          volumes:
            - ./project-W-data/:/etc/xdg/project-W/
          environment:
            - SECRET_KEY=${PROJECT_W_SECRET_KEY}
            - POSTGRES_PASSWORD=${PROJECT_W_POSTGRES_PASSWORD}
            - ADMIN_PASSWORD=${PROJECT_W_ADMIN_PASSWORD}

6. Generate a SECRET_KEY that will be used to for encrypting database contents and signing tokens. If you have python installed you can use the following command for this:

   .. code-block:: console

      python -c 'import secrets; print(secrets.token_hex(32))'

7. Run the containers. Replace <Secret Key>, <Your SMTP Password>, <Postgres password> and <project-w admin user password> with the SECRET_KEY we generated before, the password of the SMTP Server you want to use, some secure password that the admin user should have, and some secure password that you want to use for Postgresql respectively:

   .. code-block:: console

      PROJECT_W_SECRET_KEY="<Secret Key>" PROJECT_W_SMTP_PASSWORD="<Your SMTP Password>" PROJECT_W_POSTGRES_PASSWORD="<Postgres password>" PROJECT_W_ADMIN_PASSWORD="<project-w admin user password>" docker compose up -d

8. You may want to set up some kind of backup solution. For this you just need to backup the project-W-data directory (which will include the database, your ssl certificate and your config.yml) and maybe your docker-compose.yml if you made changes to it.


.. _reverse_proxy-label:

With Reverse Proxy
''''''''''''''''''

Follow this guide if you want to run this behind a Reverse Proxy which automatically takes care of SSL. This setup will disable https on the backend itself but enable it on the reverse proxy. Please make sure that your users only access the Project-W backend through the reverse proxy in this setup, otherwise their traffic will be unencrypted leaving sensitive data, passwords and token open to attackers!

.. attention::
   This guide will make use of the caddy webserver because of it's automatic handling of https. If you choose to not use caddy as your reverse proxy though then please make sure that your reverse proxy is properly configured to handle the upload of large files. The backend can handle files of many GiB or even larger, limiting this in your reverse proxy will hinder the submission of jobs and present the user with possibly confusing error messages! We will not cover the configuration of the reverse proxy here, but for example if you use nginx you will want to set ``client_max_body_size 0;`` in your config.

1. Install Docker: Refer to your distros package manager / the `Docker documentation <https://docs.docker.com/engine/install/>`_ for this
2. Create initial directory structure and enter project-W directory:

   .. code-block:: console

      mkdir -p project-W/project-W-data && mkdir -p project-W/caddy-data/data && mkdir project-W/caddy-data/config && mkdir project-W/caddy-data/conf && cd project-W

3. Configure Caddy by creating the file called Caddyfile under caddy-data/conf/ with the following content. Please make sure that the DNS record of this domain points to the docker host and that all firewalls and NATs you may have in place are configured to allow traffic on ports 80 AND 443 to the docker host from the internet.

   .. code-block::

      <the domain under which the backend should be served>

      #configure hsts
      header Strict-Transport-Security "max-age=31536000; includeSubdomains; preload"
      #compression
      encode zstd gzip
      reverse_proxy project-w:5000

4. To run the backend you need a config.yml file that configures it. You can start off with the following example (don't forget to replace the <placeholders>!) and modify it to your needs if necessary. Put this config file into ./project-W-data. Refer to :doc:`example_configs` for more advanced example configs and to :ref:`description_backend_config-label` for more information about all the configuration options.

   .. warning::
      Please make sure to store the secrets that are read from an environment variable here in a secret way on your server! Some of these secrets would allow a bad actor to gain full access to Project-W including access to all user data!

      Please also make sure to not loose your secret_key as it is used for encrypting contents of the database. Refer to :doc:`database_encryption` for more information on that.

   In this setup, the secret_key and the smtp password are being read from the environment variables 'PROJECT_W_SECRET_KEY' and 'PROJECT_W_SMTP_PASSWORD'. If you want you can also choose to set them here directly in the config, but if you do so please take appropriate measures to keep this config file secret!

   .. code-block:: yaml

      client_url: https://<your domain>/#
      web_server:
        allowed_hosts:
          - localhost
          - <your domain>
        reverse_proxy:
          trusted_proxies:
            - "172.16.0.0/12" #private ip range used by docker by default
        no_https: true
      postgres_connection_string: !ENV 'postgresql://project_w:${POSTGRES_PASSWORD}@postgres:5432/project_w'
      redis_connection:
        connection_string: 'redis://redis:6379/project-W'
      security:
        secret_key: !ENV ${SECRET_KEY}
        local_account:
          user_provisioning:
            0:
              email: <email of your admin user>
              password: !ENV ${ADMIN_PASSWORD}
              is_admin: true
      smtp_server:
        hostname: <your smtp servers domain>
        port: <smtp port of smtp server>
        secure: <starttls or ssl>
        sender_email: <email address that should send emails to your users>
        username: <probably same as above>
        password: !ENV ${SMTP_PASSWORD}

5. Put docker-compose.yml in the current directory. Use the following config and make same adjustments if needed (make sure to replace the <placeholders>!):

   .. code-block:: yaml

      services:
        postgres:
          image: postgres:17
          restart: unless-stopped
          environment:
            - POSTGRES_USER=project_w
            - POSTGRES_PASSWORD=${PROJECT_W_POSTGRES_PASSWORD}
          healthcheck:
            test: ["CMD-SHELL", "pg_isready -U project_w -d project_w"]
            interval: 10s
            retries: 3
            start_period: 30s
            timeout: 10s
          volumes:
            - ./project-W-data/postgres/:/var/lib/postgresql/data
        redis:
          image: redis:8
          restart: unless-stopped
          healthcheck:
            test: ["CMD", "redis-cli", "ping"]
            interval: 10s
            retries: 3
            start_period: 30s
            timeout: 10s
        project-w:
          image: ghcr.io/ssciwr/project-w
          restart: unless-stopped
          depends_on:
            postgres:
              condition: service_healthy
            redis:
              condition: service_healthy
          healthcheck:
            test: ["CMD", "curl", "-fk", "http://localhost:5000/api/about"]
            interval: 10s
            retries: 3
            start_period: 30s
            timeout: 10s
          volumes:
            - ./project-W-data/:/etc/xdg/project-W/
          environment:
            - SECRET_KEY=${PROJECT_W_SECRET_KEY}
            - SMTP_PASSWORD=${PROJECT_W_SMTP_PASSWORD}
            - POSTGRES_PASSWORD=${PROJECT_W_POSTGRES_PASSWORD}
            - ADMIN_PASSWORD=${PROJECT_W_ADMIN_PASSWORD}
        project-w_cron:
          image: ghcr.io/ssciwr/project-w_cron
          restart: unless-stopped
          depends_on:
            postgres:
              condition: service_healthy
            redis:
              condition: service_healthy
          volumes:
            - ./project-W-data/:/etc/xdg/project-W/
          environment:
            - SECRET_KEY=${PROJECT_W_SECRET_KEY}
            - POSTGRES_PASSWORD=${PROJECT_W_POSTGRES_PASSWORD}
            - ADMIN_PASSWORD=${PROJECT_W_ADMIN_PASSWORD}
        caddy:
          image: caddy:2
          restart: unless-stopped
          cap_add:
            - NET_ADMIN
          depends_on:
            project-w:
              condition: service_healthy
          volumes:
            - ./caddy-data/data:/data
            - ./caddy-data/config:/config
            - ./caddy-data/conf:/etc/caddy
          ports:
            - 80:80
            - 443:443
            - 443:443/udp

6. Generate a SECRET_KEY that will be used to for encrypting database contents and signing tokens. If you have python installed you can use the following command for this:

   .. code-block:: console

      python -c 'import secrets; print(secrets.token_hex(32))'

7. Run the containers. Replace <Secret Key>, <Your SMTP Password>, <Postgres password> and <project-w admin user password> with the SECRET_KEY we generated before, the password of the SMTP Server you want to use, some secure password that the admin user should have, and some secure password that you want to use for Postgresql respectively:

   .. code-block:: console

      PROJECT_W_SECRET_KEY="<Secret Key>" PROJECT_W_SMTP_PASSWORD="<Your SMTP Password>" PROJECT_W_POSTGRES_PASSWORD="<Postgres password>" PROJECT_W_ADMIN_PASSWORD="<project-w admin user password>" docker compose up -d

8. You may want to set up some kind of backup solution. For this you just need to backup the project-W-data directory (which will include the database and your config.yml), the caddy-data directory (which will include your ssl certs, ocsp staples and so on) and maybe your docker-compose.yml if you made changes to it.

Runner
``````

The runner runs the whisper model and thus benefits greatly from running on a GPU, which we heavily recommend. This GPU should have at least 10GB of VRAM available, ideally a bit more. If you don't have a powerful enough GPU available though you can choose to also run it on CPU. Choose between the following instructions depending on your choice. Currently we have only instructions for NVIDIA GPUs using CUDA but it should also be possible to run this on an AMD GPU using ROCM (for this you are on your own though).

NVIDIA GPU
''''''''''

1. If you don't already have one then create an hugging face account, then using that account accept the conditions for the `pyannote/segmentation-3.0 <https://huggingface.co/pyannote/segmentation-3.0>`_ and `pyannote/speaker-diarization-3.1 <https://huggingface.co/pyannote/speaker-diarization-3.1>`_ models. Create a token with access permissions to these repositories (e.g. by just granting the 'Read access to contents of all public gated repos you can access' permission).

2. Install Docker: Refer to your distros package manager / the `Docker documentation <https://docs.docker.com/engine/install/>`_ for this

3. Install the NVIDIA container toolkit. Refer to the `NVIDIA toolkit documentation <https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>`_ for this. Don't forget to restart your docker daemon afterwards.

4. Create initial directory structure and enter project-w directory:

   .. code-block:: console

      mkdir -p project-W/runner-config && mkdir project-W/runner-models && cd project-W

5. Like for the backend you also need a config.yml file for the runner. Prepare this file before following the installation steps below. You can use the following example as a base (don't forget to replace the <placeholder>!) and modify it to your needs if necessary. Put this file into ./runner-config. Refer to :ref:`description_runner_config-label` for more information about all the configuration options of the runner.

   .. warning::
      Please make sure to store the secrets that are read from an environment variable here in a secret way on your server! Some of these secrets would allow a bad actor to gain full access to Project-W including access to all user data!

   In this setup, the auth token and the hugging face token are read from the environment variable 'PROJECT_W_AUTH_TOKEN' and 'PROJECT_W_HF_TOKEN' respectively. If you want you can also choose to set it directly in the config, but if you do so please take appropriate measures to keep this config file secret!

   .. code-block:: yaml

      runner_attributes:
        name: "<name of your runner how it should be shown to your users>"
      backend_settings:
        url: https://<domain of your Project-W backend>
        auth_token: !ENV ${AUTH_TOKEN}
      whisper_settings:
        hf_token: !ENV ${HF_TOKEN}


6. Put docker-compose.yml in the current directory. Use the following config and make adjustments if needed

   .. code-block:: yaml

      services:
        runner:
          image: ghcr.io/ssciwr/project-w_runner
          restart: unless-stopped
          volumes:
            - ./runner-config:/etc/xdg/project-W-runner/
            - ./runner-models:/root/.cache/project-W-runner/
          environment:
            - AUTH_TOKEN=${PROJECT_W_AUTH_TOKEN}
            - HF_TOKEN=${PROJECT_W_HF_TOKEN}
          deploy:
            resources:
              reservations:
                devices:
                  - driver: nvidia
                    count: 1
                    capabilities: [gpu]

   .. note::
      Alternatively if you have a system with multiple GPUs and you want to have more control over which GPU gets allocated to the Runner, you can replace 'count: 1' above with 'count: all' and then select the GPU in the config.yml using the 'whisper_settings.torch_device' option. See :ref:`description_runner_config-label`.

7. Create a new Runner and obtain its runner token. Refer to :doc:`connect_runner_backend` for how to do that.

8. Run the container. Replace <Runner Token> with the runner token you obtained from the backend in the previous step:

   .. code-block:: console

      PROJECT_W_AUTH_TOKEN="<obtained runner token>" PROJECT_W_HF_TOKEN="<your huggingface token>" docker compose up -d

9. You may want to back up the runners config file (in ./runner-config) and the docker-compose.yml file if you made any changes to them. The ./runner-models directory contains all the whisper models that the runner will fetch automatically. You don't need to backup this directory but you can keep this directory around, copy it to other machines and share it between runners so that the runner doesn't need to spend time fetching these models anymore and so that if you have multiple runners on the same machine the models don't take up storage space multiple times!

CPU
'''

1. If you don't already have one then create an hugging face account, then using that account accept the conditions for the `pyannote/segmentation-3.0 <https://huggingface.co/pyannote/segmentation-3.0>`_ and `pyannote/speaker-diarization-3.1 <https://huggingface.co/pyannote/speaker-diarization-3.1>`_ models. Create a token with access permissions to these repositories (e.g. by just granting the 'Read access to contents of all public gated repos you can access' permission).

2. Install Docker: Refer to your distros package manager / the `Docker documentation <https://docs.docker.com/engine/install/>`_ for this

3. Create initial directory structure and enter project-w directory:

   .. code-block:: console

      mkdir -p project-W/runner-config && mkdir project-W/runner-models && cd project-W

4. Like for the backend you also need a config.yml file for the runner. Prepare this file before following the installation steps below. You can use the following example as a base (don't forget to replace the <placeholder>!) and modify it to your needs if necessary. Put this file into ./runner-config. Refer to :ref:`description_runner_config-label` for more information about all the configuration options of the runner.

   .. warning::
      Please make sure to store the secrets that are read from an environment variable here in a secret way on your server! Some of these secrets would allow a bad actor to gain full access to Project-W including access to all user data!

   In this setup, the auth token and the hugging face token are read from the environment variable 'PROJECT_W_AUTH_TOKEN' and 'PROJECT_W_HF_TOKEN' respectively. If you want you can also choose to set it directly in the config, but if you do so please take appropriate measures to keep this config file secret!

   .. code-block:: yaml

      runner_attributes:
        name: "<name of your runner how it should be shown to your users>"
      backend_settings:
        url: https://<domain of your Project-W backend>
        auth_token: !ENV ${AUTH_TOKEN}
      whisper_settings:
        hf_token: !ENV ${HF_TOKEN}
        torch_device: cpu
        compute_type: int8
        batch_size: 4


5. Put docker-compose.yml in the current directory. Use the following config and make adjustments if needed

   .. code-block:: yaml

      services:
        runner:
          image: ghcr.io/ssciwr/project-w_runner
          restart: unless-stopped
          volumes:
            - ./runner-config:/etc/xdg/project-W-runner/
            - ./runner-models:/root/.cache/project-W-runner/
          environment:
            - AUTH_TOKEN=${PROJECT_W_AUTH_TOKEN}
            - HF_TOKEN=${PROJECT_W_HF_TOKEN}

6. Create a new Runner and obtain its runner token. Refer to :doc:`connect_runner_backend` for how to do that.

7. Run the container. Replace <Runner Token> with the runner token you obtained from the backend in the previous step:

   .. code-block:: console

      PROJECT_W_AUTH_TOKEN="<obtained runner token>" PROJECT_W_HF_TOKEN="<your huggingface token>" docker compose up -d

8. You may want to back up the runners config file (in ./runner-config) and the docker-compose.yml file if you made any changes to them. The ./runner-models directory contains all the whisper models that the runner will fetch automatically. You don't need to backup this directory but you can keep this directory around, copy it to other machines and share it between runners so that the runner doesn't need to spend time fetching these models anymore and so that if you have multiple runners on the same machine the models don't take up storage space multiple times!

NixOS
-----

We provide a NixOS flake in the Project-W repository. This flake provides development shells, packages as well as NixOS modules for all three subprojects. While the runner package does theoretically come with all dependencies for proper NVIDIA/CUDA support, it hasn't been tested yet if it actually runs on GPU.

To start you need to add our flake as an input to your own. For this add the following to your 'inputs' section in your flake.nix:

   .. code-block:: Nix

      inputs = {
        ...
        project-W.url = "github:ssciwr/project-W";
      };

Note that overriding the `nixpkgs` input of the flake might not work, especially if you are using a stable branch of nixpkgs. Your mileage may vary.

If you want to use our NixOS module, you need to import it, e.g.:

   .. code-block:: Nix

      nixosConfiguration.<your machines hostname> = nixpkgs.lib.nixosSystem {
        ...
        modules = [
          inputs.project-W.nixosModules.default
          ...
        ];
      };

You might also want to add the nix-community binary cache, especially when using the runner, since otherwise it will do some expensive builds (e.g. compiling nccl). To do this you can for example add this to your NixOS config:

   .. code-block:: Nix

      nix.settings = {
        substituters = [
          "https://nix-community.cachix.org"
        ];
        trusted-public-keys = [
          "nix-community.cachix.org-1:mB9FSh9qf2dCimDSUo8Zy7bkq5CX+/rkCWyvRCYg3Fs="
        ];
      };

Now you can start using the packages and module. It's options are available under ``services.project-W``. For a full list and description of options go to `nix/backend_module.nix` and `nix/runner_module.nix` in the project-W repository. Also the ``services.project-W.server.settings`` and ``services.project-W.runner.settings`` attribute set are just copies of the backend and runner options of the config file, so you can also refer to :ref:`description_backend_config-label` and :ref:`description_runner_config-label` for these parts. You can just use ``!ENV`` at the beginning of Nix strings as well, the module will take care that these are correctly translated into appropriate YAML. Use the ``services.project-W.server.envFile``/``services.project-W.runner.envFile`` option to pass a path to a file that sets the required environment variables. You can use secret management systems like sops-nix for this to securely manage these secrets. Don't write sensitive information into any of the options directly because then they would be world-readable in the nix store!

Backend & Frontend
``````````````````

Note that the backend module automatically sets up PostgreSQL and Redis, and connects Project-W to both of them. If you want to set that up yourself or if you want to use external instances running on different machines can disable this by setting ``services.project-W.server.enableLocalPostgres`` and ``services.project-W.server.enableLocalRedis``.

Here is an example config to get the Project-W server (backend & frontend combination) up and running (using sops-nix for secret management), using an external reverse proxy (not part of this config):

   .. code-block:: Nix

      sops.secrets."project-w_secrets" = {
        sopsFile = <path to secret file>;
        mode = "0400";
        owner = config.services.project-W.server.user;
      };

      services.project-W.server = {
        enable = true;
        settings = {
          client_url = "https://<your domain>/#";
          web_server = {
            allowed_hosts = [
              "localhost"
              "<your domain>"
            ];
            reverse_proxy.trusted_proxies = [
              "<your reverse proxy ip>"
            ];
            no_https = true;
          };
          security = {
            secret_key = "!ENV \${SECRET_KEY}";
          };
          smtp_server = {
            hostname = "<your email servers address>";
            port = 587;
            secure = "starttls";
            sender_email = "<your sender email>";
            username = "<your username email>";
            password = "!ENV \${SMTP_PASSWORD}";
          };
        };
        envFile = config.sops.secrets."project-w_secrets".path;
      };

Runner
``````

Here is an example config to get the Project-W runner up and running (using sops-nix for secret management), running on CPU:

   .. code-block:: Nix

      sops.secrets."project-w-runner_secrets" = {
        sopsFile = <path to secret file>;
        mode = "0400";
        owner = config.services.project-W.runner.user;
      };

      services.project-W.runner = {
        enable = true;
        settings = {
          runner_attributes.name = "<your runners name>";
          backend_settings = {
            url = "https://<the domain of your Project-W server>";
            auth_token = "!ENV \${AUTH_TOKEN}";
          };
          whisper_settings = {
            hf_token = "!ENV \${HF_TOKEN}";
            torch_device = "cpu";
            compute_type = "int8";
            batch_size = 4;
          };
        };
        envFile = config.sops.secrets."project-w-runner_secrets".path;
      };


.. _manual_installation-label:

Manual installation
-------------------

You can also run Project-W barebones. This can be a bit more difficult and the following steps will not be as detailed as the ones with Docker or NixOS. You will have to do stuff like configuring python virtual environments, setting up webservers or compiling the frontend yourself.

Backend, Frontend & background jobs
````````````````````````````````````

The frontend is written in Svelte and needs to be compiled into native Javascript. To do this you will need some build dependencies, however you can remove them after step 4. If you want you can even build it on a different machine and then just move the build directory to the server between step 4 and 5.

1. Install nodejs

2. Clone the repository and enter the frontend directory:

   .. code-block:: console

      git clone https://github.com/ssciwr/project-W.git & cd project-W/frontend

3. Install pnpm:

   .. code-block:: console

      npm install -g pnpm

4. Install all build dependencies:

   .. code-block:: console

      pnpm install

4. Build the frontend:

   .. code-block:: console

      pnpm build

You can find the result in the ./build directory. Now we will now setup the backend which will serve the static files inside this build directory together with the API. This way the frontend and the API are served from the same origin.

5. `Install uv <https://docs.astral.sh/uv/getting-started/installation/>`_

6. Change into the backend directory:

   .. code-block:: console

      cd ../backend

7. Install the project:

   .. code-block:: console

      uv sync

8. Spin up a Postgresql and Redis server (outside of the scope of this tutorial) and put the config.yml file of the backend either into /etc/xdg/project-W/ or ~/.config/project-W/. Alternatively you can also set a custom path to the config file using the `--custom_config_path` CLI option in the command below.

9. Run the backend server:

   .. code-block:: console

      uv run project_W --root_static_files <path to the build directory of the frontend>

10. Run the background jobs once. This command should be executed at least once each day, so you probably want add a cron job or a systemd service + timer for it:

   .. code-block:: console

      uv run project_W --run_periodic_tasks


Runner
``````

1. `Install uv <https://docs.astral.sh/uv/getting-started/installation/>`_ and ffmpeg.

2. Clone the repository and enter the runner directory:

   .. code-block:: bash

      git clone https://github.com/ssciwr/project-W.git & cd project-W/runner

3. Install the project including it's whisper dependencies:

   .. code-block:: bash

      uv sync --all-extras

4. Put the config.yml file of the runner either into /etc/xdg/project-W-runner/ or ~/.config/project-W-runner/. Alternatively you can also set a custom path to the config file using the `--custom_config_path` CLI option in the command below.

5. Start up the runner:

   .. code-block:: bash

      uv run project_W_runner

6. You may want to make sure that the runner will always restart itself even if it crashes. Currently this might happen in rare cases, so maybe write a script or a systemd service that will always automatically restart the runner in case of a crash.
