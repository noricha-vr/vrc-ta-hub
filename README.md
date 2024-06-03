# Google Cloud Run auto deploy example

This is example for auto deploy for Google Cloud Run with GitHub.

1. [Enable Cloud Run](https://console.cloud.google.com/run)
1. [Enable Cloud Registry](https://console.cloud.google.com/gcr/images/)
1. [Enable Cloud Run Administrator Status](https://console.cloud.google.com/cloud-build/settings/service-account)
1. [Connect your GitHub repository.Then add trigger](https://console.cloud.google.com/cloud-build/triggers)
1. Push any change to git branch! It's build automatically `build` and `deploy` on Cloud Run!

You can change Cloud Run setting in `cloudbuild.yml`.

If you want to change service name, replace `vrc-ta-hub` to your service name.

Default options

* --allow-unauthenticated (Allow from pulic access)
* --region asia-northeast1 (select [region](https://cloud.google.com/compute/docs/regions-zones))
* --memory 1024Mi (setting memory size. 256Mi 512Mi 2028Mi etc)
* --image gcr.io/$PROJECT_ID/vrc-ta-hub (select run container)
* --platform managed (managed is run on Cloud Run)

## Setup

### Step 1, Rest App

```
cd cloud-run-django
reset_app.sh
rm reset_app.sh
cd app
python3 manage.py startapp YOUR_APP_NAME
```

### Step 2, Settings.py

Replace ALLOWED_HOSTS by this line.

```
ALLOWED_HOSTS.append(os.environ.get('HTTP_HOST'))
```

Change language settings.

```
LANGUAGE_CODE = 'ja'

TIME_ZONE = 'Asia/Tokyo'
```

Add DB logic.

```
import sys
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': '',
        'USER': '',
        'PASSWORD': '',
        'HOST': '',
    },
    'OPTIONS': {
        'init_command'
        '': 'SET default_storage_engine=INNODB',

    }
}
if DEBUG:
    logger.info('Local DB')

    if 'test' in sys.argv:
        DATABASES['default']['ENGINE'] = 'django.db.backends.sqlite3'
    else:
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.mysql',
                'HOST': 'db',
                'USER': '', # login user name
                'PASSWORD': '', 
                'NAME': '',  # database name
                'PORT': 3306
            },
        }
```

Activate your app.

```
INSTALLED_APPS = [
    'APP_NAME.apps.APP_NAMEConfig',
    'django.contrib.admin',
    'django.contrib.auth',
    ...
]
```

### Stop 3, Rname Project

Rename `vrc-ta-hub` to YOUR_PROJECT_NAME

```
python set_project_name.py YOUR_PROJECT_NAME
```

This function renames these files.

- docker-compose
- cloudbuild.yaml
- README.md

### Step 4, Add modules

Frequently used modules

```
mysqlclient
requests
```


### Step 5, Build and Migrate DB

```shell script
docker build . -t vrc-ta-hub
docker exec -it vrc-ta-hub bash
python manage.py makemigrations && python manage.py migrate
```

### Step 6, Pycharm interpreter setting.

1. Press `⌘ + ,` add `docker-compose` interpreter.
2. Open `django` setting.
3. Set project directry.
4. select  `settings.py`.

### Step 7, Run Configurations 

1. Add `DjangoServer`
2. Set host  as `0.0.0.0`
3. Set port as `8080`
4. Check Run browser http://0.0.0.0:YOUR_MACHINE_PORT/
5. Change YOUR_MACHINE_PORT to your local maschine port on `docker-compose.yml`

```
    ports:
      - 'YOUR_MACHINE_PORT:8080'
```

### Step 8, Push and Build

```
git commit -m "init"
git push
```

1. Open GCP CloudBuid.
1. Add repository
2. Add triger
3. Run

### Step 9, Set ENVIRONMENT

1. Open Cloud Run.
2. Copy service Domain from URL.
3. Edit revision of variable
4. Set `HTTP_HOST` and your copied domain.
5. Access service URL.
6. Run `gcloud config set builds/use_kaniko True` in terminal to use Kaniko.

### Step 10 Set Pycharm project root.

1. Right click `app` folder.
1. Select as project root.

