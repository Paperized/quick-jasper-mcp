package itas.jrxml;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.awt.Image;
import java.awt.image.BufferedImage;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Collection;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import javax.imageio.ImageIO;
import net.sf.jasperreports.engine.JRDataSource;
import net.sf.jasperreports.engine.JREmptyDataSource;
import net.sf.jasperreports.engine.JRException;
import net.sf.jasperreports.engine.JRParameter;
import net.sf.jasperreports.engine.JasperCompileManager;
import net.sf.jasperreports.engine.JasperFillManager;
import net.sf.jasperreports.engine.JasperPrint;
import net.sf.jasperreports.engine.JasperPrintManager;
import net.sf.jasperreports.engine.JasperReport;
import net.sf.jasperreports.engine.data.JRMapCollectionDataSource;
import net.sf.jasperreports.engine.data.JRXmlDataSource;
import net.sf.jasperreports.export.SimpleExporterInput;
import net.sf.jasperreports.export.SimpleOutputStreamExporterOutput;
import net.sf.jasperreports.pdf.JRPdfExporter;
import net.sf.jasperreports.pdf.SimplePdfExporterConfiguration;

public final class JrxmlToolRunner {
    private JrxmlToolRunner() {
    }

    private static void printUsage() {
        System.out.println("JrxmlToolRunner");
        System.out.println("Usage:");
        System.out.println("  validate <jrxmlPath>");
        System.out.println("  render <jrxmlPath> <outputPath> <png|pdf> <dataPath|-> <none|json|xml> <pageIndex> [locale] [paramsJsonPath|-] [pdfMetadataPath|-]");
        System.out.println("  -help | --help");
    }

    public static void main(String[] args) {
        if (args.length == 0) {
            printUsage();
            System.exit(2);
        }
        if ("-help".equalsIgnoreCase(args[0]) || "--help".equalsIgnoreCase(args[0])) {
            printUsage();
            return;
        }
        if (args.length < 2) {
            printUsage();
            System.exit(2);
        }
        try {
            String action = args[0];
            if ("validate".equalsIgnoreCase(action)) {
                validate(args[1]);
                System.out.println("VALIDATION_OK");
                return;
            }
            if ("render".equalsIgnoreCase(action)) {
                if (args.length < 7) {
                    throw new IllegalArgumentException("render requires 6 arguments");
                }
                String localeTag = args.length >= 8 ? args[7] : "it_IT";
                String paramsPath = args.length >= 9 ? args[8] : "-";
                String pdfMetadataPath = args.length >= 10 ? args[9] : "-";
                render(args[1], args[2], args[3], args[4], args[5], Integer.parseInt(args[6]), localeTag, paramsPath, pdfMetadataPath);
                System.out.println("RENDER_OK");
                return;
            }
            throw new IllegalArgumentException("Unsupported action: " + action);
        } catch (Throwable t) {
            t.printStackTrace(System.err);
            System.exit(1);
        }
    }

    private static void validate(String jrxmlPath) throws JRException {
        JasperCompileManager.compileReport(jrxmlPath);
    }

    private static void render(
            String jrxmlPath,
            String outputPath,
            String format,
            String dataPath,
            String dataType,
            int pageIndex,
            String localeTag,
            String paramsPath,
            String pdfMetadataPath) throws Exception {
        JasperReport report = JasperCompileManager.compileReport(jrxmlPath);
        JRDataSource dataSource = buildDataSource(dataPath, dataType);
        HashMap<String, Object> params = new HashMap<>();
        params.put(JRParameter.REPORT_LOCALE, parseLocale(localeTag));
        params.putAll(loadReportParameters(paramsPath));
        JasperPrint print = JasperFillManager.fillReport(report, params, dataSource);
        String normalized = format.toLowerCase(Locale.ROOT);
        if ("pdf".equals(normalized)) {
            exportPdf(print, outputPath, loadPdfMetadata(pdfMetadataPath));
            return;
        }
        if ("png".equals(normalized)) {
            if (print.getPages() == null || print.getPages().isEmpty()) {
                throw new JRException("Report generated zero pages");
            }
            int boundedPage = Math.max(0, Math.min(pageIndex, print.getPages().size() - 1));
            Image image = JasperPrintManager.printPageToImage(print, boundedPage, 2.0f);
            BufferedImage buffered = toBufferedImage(image);
            ImageIO.write(buffered, "png", new File(outputPath));
            return;
        }
        throw new IllegalArgumentException("Unsupported format: " + format);
    }

    private static JRDataSource buildDataSource(String dataPath, String dataType) throws Exception {
        String normalized = dataType == null ? "none" : dataType.toLowerCase(Locale.ROOT);
        if ("none".equals(normalized) || dataPath == null || "-".equals(dataPath)) {
            return new JREmptyDataSource(1);
        }
        if ("json".equals(normalized)) {
            return buildJsonMapDataSource(dataPath);
        }
        if ("xml".equals(normalized)) {
            return new JRXmlDataSource(dataPath, "/");
        }
        throw new IllegalArgumentException("Unsupported mock data type: " + dataType);
    }

    private static JRDataSource buildJsonMapDataSource(String dataPath) throws Exception {
        String json = new String(Files.readAllBytes(Paths.get(dataPath)), StandardCharsets.UTF_8);
        ObjectMapper mapper = new ObjectMapper();
        Object parsed = mapper.readValue(json, Object.class);
        Collection<Map<String, ?>> rows = new ArrayList<>();

        if (parsed instanceof Map<?, ?>) {
            Map<?, ?> rootMap = (Map<?, ?>) parsed;
            Object rowsNode = rootMap.get("rows");
            if (rowsNode instanceof List<?>) {
                List<?> listNode = (List<?>) rowsNode;
                for (Object item : listNode) {
                    if (item instanceof Map<?, ?>) {
                        Map<?, ?> itemMap = (Map<?, ?>) item;
                        rows.add(castToStringObjectMap(itemMap));
                    }
                }
            } else {
                rows.add(castToStringObjectMap(rootMap));
            }
        } else if (parsed instanceof List<?>) {
            List<?> parsedList = (List<?>) parsed;
            for (Object item : parsedList) {
                if (item instanceof Map<?, ?>) {
                    Map<?, ?> itemMap = (Map<?, ?>) item;
                    rows.add(castToStringObjectMap(itemMap));
                }
            }
        }

        if (rows.isEmpty()) {
            rows.add(new HashMap<>());
        }
        return new JRMapCollectionDataSource(rows);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> castToStringObjectMap(Map<?, ?> map) {
        return (Map<String, Object>) map;
    }

    private static Locale parseLocale(String localeTag) {
        if (localeTag == null || localeTag.trim().isEmpty()) {
            return Locale.ITALY;
        }
        String normalized = localeTag.replace('-', '_');
        String[] parts = normalized.split("_");
        if (parts.length == 1) {
            return new Locale(parts[0]);
        }
        if (parts.length == 2) {
            return new Locale(parts[0], parts[1]);
        }
        return new Locale(parts[0], parts[1], parts[2]);
    }

    private static Map<String, Object> loadReportParameters(String paramsPath) throws Exception {
        if (paramsPath == null || paramsPath.trim().isEmpty() || "-".equals(paramsPath)) {
            return Collections.emptyMap();
        }
        String json = new String(Files.readAllBytes(Paths.get(paramsPath)), StandardCharsets.UTF_8);
        ObjectMapper mapper = new ObjectMapper();
        Map<String, Object> params = mapper.readValue(json, new TypeReference<Map<String, Object>>() {});
        return params == null ? Collections.<String, Object>emptyMap() : params;
    }

    private static Map<String, Object> loadPdfMetadata(String metadataPath) throws Exception {
        if (metadataPath == null || metadataPath.trim().isEmpty() || "-".equals(metadataPath)) {
            return Collections.emptyMap();
        }
        String json = new String(Files.readAllBytes(Paths.get(metadataPath)), StandardCharsets.UTF_8);
        ObjectMapper mapper = new ObjectMapper();
        Map<String, Object> metadata = mapper.readValue(json, new TypeReference<Map<String, Object>>() {});
        return metadata == null ? Collections.<String, Object>emptyMap() : metadata;
    }

    private static void exportPdf(JasperPrint print, String outputPath, Map<String, Object> metadata) throws JRException {
        JRPdfExporter exporter = new JRPdfExporter();
        exporter.setExporterInput(new SimpleExporterInput(print));
        exporter.setExporterOutput(new SimpleOutputStreamExporterOutput(outputPath));

        SimplePdfExporterConfiguration config = new SimplePdfExporterConfiguration();
        if (metadata != null) {
            Object title = metadata.get("title");
            Object author = metadata.get("author");
            Object subject = metadata.get("subject");
            Object keywords = metadata.get("keywords");
            Object creator = metadata.get("creator");
            Object tagged = metadata.get("tagged");
            Object tagLanguage = metadata.get("tag_language");
            Object displayMetadataTitle = metadata.get("display_metadata_title");
            Object compressed = metadata.get("compressed");

            if (title != null) config.setMetadataTitle(String.valueOf(title));
            if (author != null) config.setMetadataAuthor(String.valueOf(author));
            if (subject != null) config.setMetadataSubject(String.valueOf(subject));
            if (keywords != null) config.setMetadataKeywords(String.valueOf(keywords));
            if (creator != null) config.setMetadataCreator(String.valueOf(creator));
            if (tagged instanceof Boolean) config.setTagged((Boolean) tagged);
            if (tagLanguage != null) config.setTagLanguage(String.valueOf(tagLanguage));
            if (displayMetadataTitle instanceof Boolean) config.setDisplayMetadataTitle((Boolean) displayMetadataTitle);
            if (compressed instanceof Boolean) config.setCompressed((Boolean) compressed);
        }

        exporter.setConfiguration(config);
        exporter.exportReport();
    }

    private static BufferedImage toBufferedImage(Image image) {
        if (image instanceof BufferedImage) {
            return (BufferedImage) image;
        }
        BufferedImage converted = new BufferedImage(
                image.getWidth(null),
                image.getHeight(null),
                BufferedImage.TYPE_INT_ARGB
        );
        converted.getGraphics().drawImage(image, 0, 0, null);
        return converted;
    }
}
