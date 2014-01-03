SCRIPTS=pq.bash

install:
	python setup.py install
	install -o root -g root -p -m0755 $(SCRIPTS) /usr/local/bin
