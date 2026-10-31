function write_json(path, value)
% WRITE_JSON  value, a struct of strings, numbers, logicals and cell arrays
% of strings, to path as JSON (R2018a has jsonencode)
stream = fopen(path, 'w', 'n', 'UTF-8');
fwrite(stream, jsonencode(value), 'char');
fclose(stream);
end
