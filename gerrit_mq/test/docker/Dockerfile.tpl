FROM ubuntu:trusty

MAINTAINER Josh Bialkowski <josh.bialkowski@gmail.com>

# The username gerrit2 comes from the Gerrit installation documenation.
ENV GERRIT_USER gerrit2
ENV GERRIT_UID  {{dockuser_id}}
ENV GERRIT_HOME /home/${GERRIT_USER}
ENV GERRIT_WAR ${GERRIT_HOME}/gerrit.war
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y \
            openjdk-7-jre-headless \
            git-core \
            python \
            vim


# configure gerrit user
# ------------------------

RUN groupadd -g ${GERRIT_UID} ${GERRIT_USER} \
 && useradd -d ${GERRIT_HOME} \
            -u ${GERRIT_UID} \
            -g ${GERRIT_UID} \
            -s /bin/bash \
            -m ${GERRIT_USER}

ADD gerrit.config $GERRIT_HOME/gerrit/etc/gerrit.config
ADD gerrit.war ${GERRIT_WAR}
ADD ssh ${GERRIT_HOME}/.ssh
ADD start.sh ${GERRIT_HOME}/start.sh
# ADD secure.config $GERRIT_HOME/gerrit/etc/secure.config

RUN chown -R ${GERRIT_USER}:${GERRIT_USER} ${GERRIT_HOME} \
    && chmod 700 ${GERRIT_HOME}/.ssh \
    && chmod 600 ${GERRIT_HOME}/.ssh/*

# Allow the gerrit2 user to run commands as root without a password
RUN echo "${GERRIT_USER} ALL = (root) NOPASSWD: ALL" > /etc/sudoers.d/gerrit_all \
    && chmod 440 /etc/sudoers.d/gerrit_all


# configure gerrit service
# ------------------------

USER gerrit2
WORKDIR /home/gerrit2

ENV JAVA_HOME /usr/lib/jvm/java-7-openjdk-amd64/jre
RUN java -jar $GERRIT_WAR init --batch -d ${GERRIT_HOME}/gerrit
RUN java -jar $GERRIT_WAR reindex -d ${GERRIT_HOME}/gerrit

USER root

# Gerrit will generate a self-signed certificate as part of --batch init, but
# it will generate and use a random ssl key, overwriting the one we provided in
# secure.config. We need to re-addsecure.config so that gerrit knows the
# password for the keystore.
# Copy keystore file with temporary SSL cert.  Cert was generated with:
#   keytool -keystore keystore -alias jetty -genkey -keyalg RSA
# ENV GERRIT_KEYSTORE $GERRIT_HOME/gerrit/etc/keystore
# ADD keystore ${GERRIT_KEYSTORE}
# ADD secure.config $GERRIT_HOME/gerrit/etc/secure.config

# RUN chown -R ${GERRIT_USER}:${GERRIT_USER} ${GERRIT_KEYSTORE}


# initialize gerrit
# ------------------------
USER gerrit2

# Add an ssh key to the admin user
ADD h2.jar ${GERRIT_HOME}/h2.jar
ADD init_db.sql ${GERRIT_HOME}/init_db.sql
RUN java -cp ${GERRIT_HOME}/h2.jar org.h2.tools.RunScript \
    -url jdbc:h2:${GERRIT_HOME}/gerrit/db/ReviewDB \
    -script ${GERRIT_HOME}/init_db.sql

# Start gerrit and run the init script which creates a group,
# and writes the All-Projects config
ADD all_projects.config ${GERRIT_HOME}/all_projects.config
ADD init_gerrit.py ${GERRIT_HOME}/init_gerrit.py
RUN ${GERRIT_HOME}/gerrit/bin/gerrit.sh start \
    && sleep 3 \
    && ${GERRIT_HOME}/init_gerrit.py \
    && ${GERRIT_HOME}/gerrit/bin/gerrit.sh stop

# cleanup
# ------------------------

USER root
RUN rm -rf ${GERRIT_HOME}/init_db.sql \
           ${GERRIT_HOME}/init_gerrit.py \
           ${GERRIT_HOME}/all_projects.config

EXPOSE 8443 29418
USER gerrit2
ENTRYPOINT ["/home/gerrit2/start.sh"]
