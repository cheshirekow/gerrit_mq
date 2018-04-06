import io
from setuptools import setup

GITHUB_URL = 'https://github.com/cheshirekow/gerrit_mq'

VERSION = None
with io.open('gerrit_mq/__init__.py', 'r', encoding='utf-8') as infile:
  for line in infile:
    line = line.strip()
    if line.startswith('VERSION ='):
      VERSION = line.split('=', 1)[1].strip().strip("'")

assert VERSION is not None

with io.open('README.rst', encoding='utf-8') as infile:
  long_description = infile.read()

setup(
    name='gerrit_mq',
    packages=['gerrit_mq'],
    version=VERSION,
    description="Gerrit merge serializer",
    long_description=long_description,
    author='Josh Bialkowski',
    author_email='josh.bialkowski@gmail.com',
    url=GITHUB_URL,
    download_url='{}/archive/{}.tar.gz'.format(GITHUB_URL, VERSION),
    keywords=['gerrit', 'continuous-integration'],
    classifiers=[],
    entry_points={
        'console_scripts': [
            'gerrit-mq=gerrit_mq.__main__:main'
        ],
    },
    install_requires=[
        'enum',
        'Flask',
        'httplib2',
        'jinja2',
        'pygerrit2',
        'requests',
        'sqlalchemy',
    ]
)
