package java.com.mahs4d.volcano;

import java.util.LinkedHashMap;
import java.util.Map;

public record Row(Map<String, Object> columns) {
    public Row {
        columns = new LinkedHashMap<>(columns);
    }
}
