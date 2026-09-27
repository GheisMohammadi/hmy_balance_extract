.PHONY: build build-linux test test-go test-python check clean

build:
	mkdir -p bin
	cd extractor && go build -o ../bin/independent-db .

build-linux:
	mkdir -p bin
	cd extractor && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o ../bin/independent-db-linux-amd64 .

test: test-go test-python

test-go:
	cd extractor && go test ./...

test-python:
	python3 -m unittest discover -s tests -p 'test_independent*.py'

check: test
	cd extractor && go vet ./...
	git diff --check

clean:
	rm -f bin/independent-db bin/independent-db-linux-amd64
