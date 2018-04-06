#!/usr/bin/python
"""
Build gerrit docker image and start a container from that image.
"""

from __future__ import print_function
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time

from distutils.dir_util import copy_tree
import jinja2
import requests



class ChunkReader(object):
  """
  A wrapper around a file obj for use in iter(reader.read, b'').
  """

  def __init__(self, fileobj, chunk_size):
    self.fileobj = fileobj
    self.chunk_size = chunk_size

  def read(self):
    return self.fileobj.read(self.chunk_size)


def chunk_reader(fileobj, chunk_size=4096):
  """
  Return a chunk generator for reading files.
  """

  reader = ChunkReader(fileobj, chunk_size)
  for chunk in iter(reader.read, b''):
    yield chunk


def file_hash_matches(file_path, md5sum):
  """
  Return true if the md5sum of file_path matches the given one.
  """
  hasher = hashlib.md5()
  try:
    with open(file_path, 'r') as infile:
      for chunk in chunk_reader(infile):
        hasher.update(chunk)
  except (IOError, OSError):
    return False

  return hasher.hexdigest() == md5sum


def human_readable_number_string(num, suffix='B'):
  """
  Create a human readible string from numbers that may be 'large'.
  Generates strings like: 10 MiB, 10 GiB, etc.
  """

  for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
    if abs(num) < 1024.0:
      return "%3.1f%s%s" % (num, unit, suffix)
    num /= 1024.0
  return "%.1f%s%s" % (num, 'Yi', suffix)


def download_file(url, outpath):
  """
  Downloads a file from the web.
  """
  print('  downloading {} to {}'.format(url, outpath))

  # get the total size of the file by getting requesting only the HTTP header
  # for the url
  response = requests.head(url)
  bytes_total = int(response.headers.get('content-length', default=1))

  # Now get the actual file
  response = requests.get(url, stream=True)
  bytes_received = 0
  chunk_size = 1024
  last_print_time = 0

  def print_progress():
    sys.stdout.write('\r Progress: {} / {} ({:6.2f}%)'.format(
        human_readable_number_string(bytes_received),
        human_readable_number_string(bytes_total),
        100.0 * bytes_received / bytes_total))
    sys.stdout.flush()

  with open(outpath, 'wb') as outfile:
    for chunk in response.iter_content(chunk_size=chunk_size):
      if chunk:  # filter out keep-alive new chunks
        outfile.write(chunk)
      bytes_received += chunk_size

      # Sometimes we can't really get the size a head of time
      if bytes_received > bytes_total:
        bytes_total = bytes_received

      # Print status twice a second
      now_time = time.time()
      if now_time - last_print_time > 0.5:
        last_print_time = now_time
        print_progress()

    print_progress()  # print at the end to confirm 100%
    sys.stdout.write('\n')


def download_files(build_dir, gerrit_version):
  # Download gerrit.war. Don't let docker do this because it can't cache
  # downloads
  file_list = [
      ('https://gerrit-releases.storage.googleapis.com/'
       'gerrit-{}.war'.format(gerrit_version),
       'gerrit.war', '078f6d9624508a61584a5bccf3e325bf'),
      ('http://repo2.maven.org/maven2/com/h2database/h2/'
       '1.4.192/h2-1.4.192.jar',
       'h2.jar', '8e161053d21949a13e0918550cd5d2ca'),
  ]

  for url, filename, md5sum in file_list:
    filepath = os.path.join(build_dir, filename)
    if not file_hash_matches(filepath, md5sum):
      download_file(url, filepath)


ADMIN_PRIVATE_KEY = """
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA1E3L6D/YOs2tVrOBXpy88LjPSD/IE4y39GzhXUsl4AMzHqzS
9GZfkUnWieSDd9OajRFpkMkucXyeD14bvBJYJpf90fNBmHs2NJ2gMOey0RQ9dxJd
iMrxf5mMFoiPpJ2Dof3xgYZfVXxORnYzom/oe7L59cGAovLklO2Rb9n3gEvoGOB3
gqXTpk6tnpbBbG5sdS3v/vt5MzmL79Sw8jTG4FgKo11LmO3tz4sBBsxg0zxlMYpY
LyEx18u35ec4JJaAQ0aveUtjjbtMIIpLIA1OeI3dfuaVF4A/iRB/FcVteBX7sTDx
JEcn/mjUcEjp7zFcPQN5P5MU2QjL4FhrKtyADQIDAQABAoIBAFQhvEN+QX8UELQC
EKtgq5MteZ8k/3GX2zI2j5x78jdyrShjznlhtE+MFkOw1dR/e4iZtN7WitwYST7H
yW/fSSWKJ+CyaHU3poR1Tozy1K78OVtdYMmbutYZECXB2kKe1RI14yW0DUWALDjK
EK43cMbMZzfXhdWNMb4+4tqTYvxo33ufvKz6LQTIhd+6qiVJSWuPPrDlL22fEHFl
DWV0jf6HbfqcbyPRqsDDUEUjG0UWgtZj8qGd5sxMQV6ilSgdzu8JpC4fFINp6GjY
pJGx3IiNNw+0p0W3EB8QJPCvkwb+rHoMrbm1yVnvWMhKdw9JfPiOAhxFTQ3+LVcH
qV/fU+ECgYEA6nk7U9RudDqWWnDOnnRvGwIA6YfhTd64rM9dCRu2hJp+douOmMVb
vHky5Wx7HPp3evJovB8/WHm6pYYYyu9SGGIQCsGe4g7eyGcyT+56Y76gqFEj8qKF
jWJh/VipCcv9GkU7f1Wtjsfsq61Z0+XSOlWkRKZ8yvs4eukrrum6o6kCgYEA58uE
XwsIXTGHJqbP+WlXJfOqx8Cv4uRN8uzhS/bYR5/ceLMXxaQo+1Ni6TLh9sWUWX7P
glvgJkppN7lb6IYitqtgvby85FueMIWxE23WFM1oI9nFNSCwAALv4R4/flSKAaM5
7KSafrCjQ+vl1iyxuRKV+Z6zmvzmoSvNUvSBd8UCgYA7W6uAQmQf+oD7rlkwVguW
i8BNXn/UJdEctnY4CxL+qNnhCt1zoWri4M/YssjMAkBjGKEZFtQDgvWUV8lI/qMK
1zB2eKPPWLJfi3h6McY8IBMt6TSvhSNIMqLZ7ysD9udBUkuIpdkFL2mj4IPAGAtL
h0jJtFgdTtHyk7riUftU2QKBgEocfhRNWL1DSq0HBNP/5EdUIzR+3T20NWAIcPhy
0jAEYt+Mk3szw46n2KYrCKh3/7ilnP9XFNHpVL7mWwZ7bLnvDZ1crSBuUqO8+yL1
KU+5ZSShSjZ0XxGB3uShYTepG/7uC2UoM+Vx4KGk2PWjkKdV0/Hd1hsl5S9+68Us
PADpAoGBAJZ+wPpvez1CNWAyU+0z2IoNmn/7HTY7Pw7JdhmKYNEVjX0uGz7FXQ9R
aZLftZH7rjsB8+Hgh5MAXtpUocSgIcKaDS4gdpEOJFUKXQuttKQ4hRnzOzWJ9bzD
2rHp+IHGSa9pRgdUXYrT+v698TB98jJ+oOnoFz0QRyCKZxukG+c6
-----END RSA PRIVATE KEY-----
"""

ADMIN_PUBLIC_KEY = """
ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDUTcvoP9g6za1Ws4FenLzwuM9I
P8gTjLf0bOFdSyXgAzMerNL0Zl+RSdaJ5IN305qNEWmQyS5xfJ4PXhu8Elgml/3R
80GYezY0naAw57LRFD13El2IyvF/mYwWiI+knYOh/fGBhl9VfE5GdjOib+h7svn1
wYCi8uSU7ZFv2feAS+gY4HeCpdOmTq2elsFsbmx1Le/++3kzOYvv1LDyNMbgWAqj
XUuY7e3PiwEGzGDTPGUxilgvITHXy7fl5zgkloBDRq95S2ONu0wgiksgDU54jd1+
5pUXgD+JEH8VxW14FfuxMPEkRyf+aNRwSOnvMVw9A3k/kxTZCMvgWGsq3IAN admin@test
"""

def create_admin_keypair(build_dir):
  """
  Use ssh-keygen to generate a keypair for the gerrit user inside the container
  and then return the public half of that pair.
  """

  try:
    os.makedirs(os.path.join(build_dir, 'ssh'))
  except OSError:
    pass

  pub_key = ''.join(ADMIN_PUBLIC_KEY.strip().splitlines())
  with open(os.path.join(build_dir, 'ssh/id_rsa'), 'w') as outfile:
    outfile.write(ADMIN_PRIVATE_KEY.strip())
    outfile.write('\n')
  with open(os.path.join(build_dir, 'ssh/id_rsa.pub'), 'w') as outfile:
    outfile.write(pub_key.strip())
    outfile.write('\n')

  return pub_key


def get_user_publickey():
  """
  Get the ssh public key of the current user. Not really necessary but
  useful for manual testing.
  """

  user_keyfile_path = os.path.expanduser('~/.ssh/id_rsa.pub')
  if os.path.exists(user_keyfile_path):
    with open(user_keyfile_path, 'r') as user_keyfile:
      return user_keyfile.readline().strip()
  else:
    return None


def get_template_env():
  """
  return jinja template environment
  """

  this_dir = os.path.dirname(__file__)
  docker_dir = os.path.join(this_dir, 'docker')

  template_loader = jinja2.FileSystemLoader(docker_dir)
  return jinja2.Environment(loader=template_loader)


def write_dockerfile(build_dir, uid):
  template_env = get_template_env()
  dockerfile_template = template_env.get_template('Dockerfile.tpl')
  dockerfile_config = dict(dockuser_id=uid)

  with open(os.path.join(build_dir, 'Dockerfile'), 'w') as dockerfile:
    # pylint: disable=no-member
    dockerfile.write(dockerfile_template.render(**dockerfile_config))


ADMIN_KEY_SQL = """

INSERT INTO ACCOUNT_SSH_KEYS (
  SSH_PUBLIC_KEY,
  VALID,
  ACCOUNT_ID,
  SEQ
) VALUES (
  '{key}',
  'Y',
  1000000,
  {idx}
);

"""


def write_init_sql(build_dir):
  admin_key = create_admin_keypair(build_dir)
  user_key = get_user_publickey()

  with open(os.path.join(build_dir, 'init_db.sql'), 'w') as sqlfile:
    sqlfile.write(ADMIN_KEY_SQL.format(key=admin_key, idx=1))
    if user_key:
      sqlfile.write(ADMIN_KEY_SQL.format(key=user_key, idx=2))


IMAGE_NAME = 'gerrit-mq/test'

def build_image(build_dir, gerrit_version, uid, no_rm):
  """
  Build a docker image:

  uid : The UID of the user inside the docker container. By default it will be
        the uid of the caller (files inside the volume are owned by the host
        system user).

  no_rm : Don't clean up the temporary directory that we use for building the
          docker image.
  """
  if build_dir:
    no_rm = True
  else:
    build_dir = tempfile.mkdtemp(prefix='gerrit_docker', dir='/tmp')

  print('Building gerrit image in {}'.format(build_dir))
  download_files(build_dir, gerrit_version)
  this_dir = os.path.dirname(__file__)
  copy_tree(os.path.join(this_dir, 'docker'), build_dir)
  write_dockerfile(build_dir, uid)
  write_init_sql(build_dir)
  subprocess.check_call(['docker', 'build', '-t', IMAGE_NAME, build_dir])

  if no_rm:
    print('Not cleaning up {}'.format(build_dir))
  else:
    shutil.rmtree(build_dir)

CONTAINER_NAME = 'gerrit_mq_test'

def start_container(debug, dry_run):
  """
  Start's a docker container from the corresponding image.

  dry_run : if true will print the command but not actually run it. This is
            useful for debugging if you wanted to start the container with a
            different entry point, as a non-daemon container, etc.
  """
  command = ['docker', 'run', '-ti',
             '-p', '8081:8081',
             '-p', '29418:29418',
             '-v', '/home/gerrit2/gerrit',
             '--name', CONTAINER_NAME]

  if debug:
    command.append('--rm')
  else:
    command.append('-d')

  command.append(IMAGE_NAME)

  print('Running command:')
  print('    ' + ' '.join(command))
  if not dry_run:
    print('Web interface on http://localhost:8081')
    print('Admin ssh at ssh -i <build_dir>/docker/id_rsa -p 29418 '
          'admin@localhost')
    subprocess.check_call(command)


def stop_container():
  subprocess.check_call(['docker', 'stop', CONTAINER_NAME])

def remove_container():
  subprocess.check_call(['docker', 'rm', CONTAINER_NAME])

def main():
  parser = argparse.ArgumentParser(description=__doc__)
  subparsers = parser.add_subparsers(
      help='build the test image, or control the test container',
      dest='command')

  build_parser = subparsers.add_parser('build', help='build an image')
  build_parser.add_argument(
      '-b', '--build-dir', default=None,
      help='use this directory as the docker build dir. Implies --no-rm')
  build_parser.add_argument(
      '-n', '--no-rm', action='store_true',
      help="Don't clean resources copied to docker directory after building")
  build_parser.add_argument(
      '-u', '--uid', type=int, default=os.getuid(),
      help='uid of the user inside the docker container.')
  build_parser.add_argument(
      '-g', '--gerrit-version', default='2.11.3',
      help='gerrit version string to download/install')

  start_parser = subparsers.add_parser('start', help='start a container')
  start_parser.add_argument(
      '-d', '--debug', action='store_true',
      help='If true, will run the container in the foreground and will remove'
           ' when ended')
  start_parser.add_argument(
      '-D', '--dry-run', action='store_true',
      help='Dry run, prints the command that it would run and then exits')

  subparsers.add_parser('stop', help='stop the container')
  subparsers.add_parser('rm', help='remove the container')

  args = parser.parse_args()

  if args.command == 'build':
    build_image(args.build_dir, args.gerrit_version, args.uid, args.no_rm)
  elif args.command == 'start':
    start_container(args.debug, args.dry_run)
  elif args.command == 'stop':
    stop_container()
  elif args.command == 'rm':
    remove_container()


if __name__ == '__main__':
  main()
